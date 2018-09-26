# -*- coding: utf-8 -*-
import xbmc
import time
import nakamoritools as nt
from threading import Thread

Playback_Status = ["Playing", "Paused", "Stopped"]


def log(msg):
    xbmc.log("-> nakamori.player::%s" % msg, level=xbmc.LOGNOTICE)


def sync_resume(file_id, current_time):
    if nt.addon.getSetting("syncwatched") == "true":
        nt.sync_offset(file_id, current_time)


def trakt(ep_id, status, current_time, total_time, movie, show_notification):
    if nt.addon.getSetting("trakt_scrobble") == "true":
        notification = False
        if show_notification:
            if nt.addon.getSetting("trakt_scrobble_notification") == "true":
                notification = True
        progress = int((current_time / total_time) * 100)
        nt.trakt_scrobble(ep_id, status, progress, movie, notification)


def did_i_watch_entire_episode(current_time, total_time, ep_id, user_rate):
    _finished = False
    if nt.addon.getSetting('external_player') == 'false':  # k18-alpha2 self.isExternalPlayer()
        mark = float(nt.addon.getSetting("watched_mark"))
        mark /= 100
        log('mark = %s * total = %s = %s < current = %s' % (mark, total_time, (total_time*mark), current_time))
        if (total_time * mark) < current_time:
            _finished = True
    else:
        # external set position = 1.0 when it want to mark it as watched (based on configuration of external)
        if current_time > 0.0:
            _finished = True

    if _finished:
        if nt.addon.getSetting('vote_always') == 'true':
            # convert in case shoko give float
            if user_rate == '0.0':
                nt.vote_episode(ep_id)
            else:
                log("vote_always found 'userrate' %s" % user_rate)
        params = {'ep_id': ep_id, 'watched': True}
        nt.mark_watch_status(params)


class Service(xbmc.Player):
    def __init__(self):
        log('Init')
        # xbmc.Player.__init__(self)
        self._t = None  # trakt thread
        self._s = None  # sync thread
        self._details = None
        self.Playlist = None
        self.PlaybackStatus = 'Stopped'
        self.LoopStatus = 'None'
        self.Shuffle = False
        self.Transcoded = False
        self.Metadata = {
            'shoko:fileid': '',
            'shoko:epid': '',
            'shoko:movie': '',
            'shoko:duration': 0,
            'shoko:userrate': '0.0',
            'shoko:traktonce': False,
            'shoko:rawid': '',
            'shoko:path': ''
        }
        self.CanControl = True

    def feed(self, details):
        log('feed')
        self._details = details

    def onPlayBackStarted(self):
        log('onPlaybackStarted')

        if nt.addon.getSetting('enableEigakan') == "true":
            log('set Transcoded: True')
            self.Transcoded = True

        self.Metadata['shoko:current'] = 0
        # if I recall k17 give second * 1000 and k18 give only seconds
        real_duration = int(self._details['duration'])
        self.Metadata['shoko:duration'] = real_duration if real_duration < 1000000 else real_duration/1000
        self.Metadata['shoko:epid'] = self._details['epid']
        self.Metadata['shoko:movie'] = self._details['movie']
        self.Metadata['shoko:fileid'] = self._details['fileid']
        self.Metadata['shoko:traktonce'] = True
        self.Metadata['shoko:rawid'] = self._details['rawid']

        self.PlaybackStatus = 'Playing'
        duration = self.getTotalTime()
        if self.Transcoded:
            duration = self.Metadata.get('shoko:duration')
        trakt(self.Metadata.get('shoko:epid'), 1, 0, duration, self.Metadata.get('shoko:movie'),
              self.Metadata.get('shoko:traktonce'))
        self.onPlayBackResumed()

    def onPlayBackResumed(self):
        log('onPlayBackResumed')
        self.PlaybackStatus = 'Playing'
        self.Metadata['shoko:traktonce'] = True
        try:
            self._t.stop()
        except:
            log('no trakt thread to stop')
        self._t = Thread(target=self.update_trakt, args=())
        self._t.daemon = True
        self._t.start()
        try:
            self._s.stop()
        except:
            log('no sync thread to stop')
        self._s = Thread(target=self.update_sync, args=())
        self._s.daemon = True
        self._s.start()

    def onPlayBackStopped(self):
        log('onPlayBackStopped')
        self.onPlayBackEnded()

    def onPlayBackEnded(self):
        log('onPlayBackEnded')
        # TODO userrate support
        self.Metadata['shoko:traktonce'] = True
        did_i_watch_entire_episode(self.Metadata.get('shoko:current'), self.Metadata.get('shoko:duration'),
                                   self.Metadata.get('shoko:epid'), '0.0')
        trakt(self.Metadata.get('shoko:epid'), 3, self.Metadata.get('shoko:current'),
              self.Metadata.get('shoko:duration'), self.Metadata.get('shoko:movie'),
              self.Metadata.get('shoko:traktonce'))

        if self.Transcoded:
            nt.get_json(self.Metadata.get('shoko:path') + '/cancel')

        self.Playlist = None
        self.PlaybackStatus = 'Stopped'

    def onPlayBackPaused(self):
        log('onPlayBackPaused')
        self.Metadata['shoko:traktonce'] = True
        trakt(self.Metadata.get('shoko:epid'), 2, self.Metadata.get('shoko:current'),
              self.Metadata.get('shoko:duration'), self.Metadata.get('shoko:movie'),
              self.Metadata.get('shoko:traktonce'))
        self.PlaybackStatus = 'Paused'

    def onPlayBackSeek(self, time_to_seek, seek_offset):
        log('onPlayBackSeek with %s, %s' % (time_to_seek, seek_offset))

    def update_trakt(self):
        while self.isPlayingVideo():
            self.Metadata['shoko:current'] = self.getTime()
            trakt(self.Metadata.get('shoko:epid'), 1, self.Metadata.get('shoko:current'),
                  self.Metadata.get('shoko:duration'), self.Metadata.get('shoko:movie'),
                  self.Metadata.get('shoko:traktonce'))
            self.Metadata['shoko:traktonce'] = False
            time.sleep(5)
        else:
            log("trakt_thread: not playing anything")
            return

    def update_sync(self):
        while self.isPlayingVideo():
            try:
                if nt.addon.getSetting("syncwatched") == "true" and self.getTime() > 10:
                    self.Metadata['shoko:current'] = self.getTime()
                    nt.sync_offset(self.Metadata.get('shoko:fileid'), self.Metadata.get('shoko:current'))
                    time.sleep(1)
            except:
                pass  # while buffering
        else:
            log("sync_thread: not playing anything")
            return
