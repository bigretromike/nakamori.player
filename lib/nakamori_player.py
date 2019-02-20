# -*- coding: utf-8 -*-
import xbmc
from nakamori_utils import nakamoritools as nt
from nakamori_utils.globalvars import *
from threading import Thread

Playback_Status = ["Playing", "Paused", "Stopped", "Ended"]


def log(msg):
    xbmc.log("-> nakamori.player::%s" % msg, level=xbmc.LOGNOTICE)


def scrobble_shoko(file_id, current_time):
    if plugin_addon.getSetting("syncwatched") == "true":
        nt.sync_offset(file_id, current_time)


def scrobble_trakt(ep_id, status, current_time, total_time, movie, show_notification):
    if plugin_addon.getSetting("trakt_scrobble") == "true":
        notification = False
        if show_notification:
            if plugin_addon.getSetting("trakt_scrobble_notification") == "true":
                notification = True
        progress = int((current_time / total_time) * 100)
        nt.trakt_scrobble(ep_id, status, progress, movie, notification)


def finished_episode(current_time, total_time, ep_id, user_rate, rawid):
    _finished = False
    if plugin_addon.getSetting('external_player') == 'false':
        mark = float(plugin_addon.getSetting("watched_mark"))
        mark /= 100
        log('mark = %s * total = %s = %s < current = %s' % (mark, total_time, (total_time*mark), current_time))
        if (total_time * mark) <= current_time:
            _finished = True
    else:
        # external set position = 1.0 when it want to mark it as watched (based on configuration of external)
        if current_time > 0.0:
            _finished = True

    if _finished:
        if rawid == '0':
            if plugin_addon.getSetting('vote_always') == 'true':
                # convert in case shoko give float
                if user_rate == '0.0':
                    nt.vote_episode(ep_id)
                else:
                    log("vote_always found 'userrate' %s" % user_rate)
            params = {'ep_id': ep_id, 'watched': True}
            nt.mark_watch_status(params)
        else:
            # TODO unsort files vote/watchmark support
            log('mark = watched but it was unsort file')


class Player(xbmc.Player):
    def __init__(self):
        log('Init')
        xbmc.Player.__init__(self)
        self._t = None  # trakt thread
        self._s = None  # sync thread
        self._details = None
        self.Playlist = None
        self.PlaybackStatus = 'Stopped'
        self.LoopStatus = 'None'
        self.Shuffle = False
        self.Transcoded = False
        self.Metadata = {
            'shoko:fileid': 0,
            'shoko:epid': 0,
            'shoko:movie': 0,
            'shoko:rawid': 0,
            'shoko:duration': 0,
            'shoko:userrate': '0.0',
            'shoko:traktonce': False,
            'shoko:path': ''
        }
        self.CanControl = True
        if plugin_addon.getSetting("kodi18") == 'true':
            plugin_addon.setSetting(id='external_player', value=self.isExternalPlayer())

    def reset(self):
        log('reset')
        self.__init__()

    def feed(self, details):
        log('feed')
        self._details = details

    def onPlayBackStarted(self):
        log('onPlaybackStarted')

        if plugin_addon.getSetting('enableEigakan') == "true":
            log('set Transcoded: True')
            self.Transcoded = True

        self.Metadata['shoko:current'] = 0
        # TODO if I recall k17 give second * 1000 and k18 give only seconds
        real_duration = int(self._details['duration'])
        self.Metadata['shoko:duration'] = real_duration/1000  # if real_duration < 1000000 else real_duration/1000
        self.Metadata['shoko:rawid'] = self._details.get('rawid', 0)
        self.Metadata['shoko:epid'] = self._details.get('epid', 0)
        self.Metadata['shoko:fileid'] = self._details.get('fileid', 0)
        self.Metadata['shoko:movie'] = self._details.get('movie', 0)
        self.Metadata['shoko:traktonce'] = True

        self.PlaybackStatus = 'Playing'
        # we are making the player global, so if a stop is issued, then Playing will change
        while not self.isPlaying() and self.PlaybackStatus == 'Playing':
            xbmc.sleep(100)
        if self.PlaybackStatus != 'Playing':
            return

        duration = self.getTotalTime()
        if self.Transcoded:
            duration = self.Metadata.get('shoko:duration')
        scrobble_trakt(self.Metadata.get('shoko:epid'), 1, 0, duration, self.Metadata.get('shoko:movie'),
                       self.Metadata.get('shoko:traktonce'))
        self.onPlayBackResumed()

    def onPlayBackResumed(self):
        log('onPlayBackResumed')
        self.PlaybackStatus = 'Playing'
        self.Metadata['shoko:traktonce'] = True

        self._t = Thread(target=self.tick_loop_trakt, args=())
        self._t.daemon = True
        self._t.start()

        self._s = Thread(target=self.tick_loop_shoko, args=())
        self._s.daemon = True
        self._s.start()

    def onPlayBackStopped(self):
        log('onPlayBackStopped')
        # self.onPlayBackEnded()
        self.scrobble_finished_episode()
        self.PlaybackStatus = 'Stopped'  # TODO switch them around. Ended <->Stopped

    def onPlayBackEnded(self):
        log('onPlayBackEnded')
        # TODO userrate support
        self.scrobble_finished_episode()
        self.PlaybackStatus = 'Ended'

    def onPlayBackPaused(self):
        log('onPlayBackPaused')
        self.Metadata['shoko:traktonce'] = True
        scrobble_trakt(self.Metadata.get('shoko:epid'), 2, self.Metadata.get('shoko:current'),
                       self.Metadata.get('shoko:duration'), self.Metadata.get('shoko:movie'),
                       self.Metadata.get('shoko:traktonce'))
        self.PlaybackStatus = 'Paused'

    def onPlayBackSeek(self, time_to_seek, seek_offset):
        log('onPlayBackSeek with %s, %s' % (time_to_seek, seek_offset))

    def tick_loop_trakt(self):
        if plugin_addon.getSetting("trakt_scrobble") != "true":
            return
        while self.isPlayingVideo():
            self.Metadata['shoko:current'] = self.getTime()
            scrobble_trakt(self.Metadata.get('shoko:epid'), 1, self.Metadata.get('shoko:current'),
                           self.Metadata.get('shoko:duration'), self.Metadata.get('shoko:movie'),
                           self.Metadata.get('shoko:traktonce'))
            self.Metadata['shoko:traktonce'] = False
            xbmc.sleep(2500)
        else:
            log("trakt_thread: not playing anything")
            return

    def tick_loop_shoko(self):
        while self.isPlayingVideo():
            try:
                if plugin_addon.getSetting("file_resume") == "true" and self.getTime() > 10:
                    self.Metadata['shoko:current'] = int(self.getTime())
                    nt.sync_offset(self.Metadata.get('shoko:fileid'), self.Metadata.get('shoko:current'))
                    xbmc.sleep(2500)
            except:
                pass  # while buffering
        else:
            log("sync_thread: not playing anything")
            return

    def scrobble_finished_episode(self):
        self.Metadata['shoko:traktonce'] = True
        finished_episode(self.Metadata.get('shoko:current'), self.Metadata.get('shoko:duration'),
                         self.Metadata.get('shoko:epid'), '0.0',
                         self.Metadata['shoko:rawid'])
        scrobble_trakt(self.Metadata.get('shoko:epid'), 3, self.Metadata.get('shoko:current'),
                       self.Metadata.get('shoko:duration'), self.Metadata.get('shoko:movie'),
                       self.Metadata.get('shoko:traktonce'))

        if self.Transcoded:
            nt.get_json(self.Metadata.get('shoko:path') + '/cancel')

        self.Playlist = None
