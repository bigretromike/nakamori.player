# -*- coding: utf-8 -*-
from nakamori_utils import nakamoritools as nt
from nakamori_utils.globalvars import *
from threading import Thread

from proxy.kodi_version_proxy import kodi_proxy


class PlaybackStatus(object):
    PLAYING = 'Playing'
    PAUSED = 'Paused'
    STOPPED = 'Stopped'
    ENDED = 'Ended'


def log(msg):
    xbmc.log('-> nakamori.player::%s' % msg, level=xbmc.LOGNOTICE)


def scrobble_trakt(ep_id, status, current_time, total_time, movie):
    if plugin_addon.getSetting('trakt_scrobble') == 'true':
        progress = int(current_time / total_time * 100.0)
        nt.trakt_scrobble(ep_id, status, progress, movie, False)


def finished_episode(ep_id, current_time, total_time):
    _finished = False
    if plugin_addon.getSetting('external_player') == 'false':
        mark = float(plugin_addon.getSetting('watched_mark'))
        mark /= 100
        log('mark = %s * total = %s = %s < current = %s' % (mark, total_time, (total_time*mark), current_time))
        if (total_time * mark) <= current_time:
            _finished = True
    else:
        # external set position = 1.0 when it want to mark it as watched (based on configuration of external)
        if current_time > 0.0:
            _finished = True

    if _finished:
        if int(ep_id) != 0 and plugin_addon.getSetting('vote_always') == 'true':
            # convert in case shoko give float
            nt.vote_episode(ep_id)
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
        self.is_transcoded = False
        self.is_movie = None
        self.file_id = 0
        self.ep_id = 0
        self.duration = 0
        self.path = ''
        self.scrobble = True
        self.CanControl = True
        plugin_addon.setSetting(id='external_player', value=str(kodi_proxy.external_player(self)))

    def reset(self):
        log('reset')
        self.__init__()

    def feed(self, file_id, ep_id, duration, path, scrobble):
        log('feed')
        self.file_id = file_id
        self.ep_id = ep_id
        self.duration = duration
        self.path = path
        self.scrobble = scrobble

    def onPlayBackStarted(self):
        log('onPlaybackStarted')

        if plugin_addon.getSetting('enableEigakan') == 'true':
            log('set Transcoded: True')
            self.is_transcoded = True

        # we are getting the duration in s from Shoko, so no worries about Kodi version
        duration = int(self.getTotalTime())
        if self.is_transcoded:
            duration = self.duration

        self.duration = duration

        self.PlaybackStatus = PlaybackStatus.PLAYING
        # we are making the player global, so if a stop is issued, then Playing will change
        while not self.isPlaying() and self.PlaybackStatus == PlaybackStatus.PLAYING:
            xbmc.sleep(100)
        if self.PlaybackStatus != PlaybackStatus.PLAYING:
            return

        # TODO get series and populate
        self.is_movie = False
        scrobble_trakt(self.ep_id, 1, self.getTime(), duration, self.is_movie)
        self.onPlayBackResumed()

    def onPlayBackResumed(self):
        log('onPlayBackResumed')
        self.PlaybackStatus = PlaybackStatus.PLAYING

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
        self.PlaybackStatus = PlaybackStatus.STOPPED  # TODO switch them around. Ended <->Stopped

    def onPlayBackEnded(self):
        log('onPlayBackEnded')
        # TODO userrate support
        self.scrobble_finished_episode()
        self.PlaybackStatus = PlaybackStatus.ENDED

    def onPlayBackPaused(self):
        log('onPlayBackPaused')
        self.PlaybackStatus = PlaybackStatus.PAUSED
        scrobble_trakt(self.ep_id, 2, self.getTime(), self.duration, self.is_movie)

    def onPlayBackSeek(self, time_to_seek, seek_offset):
        log('onPlayBackSeek with %s, %s' % (time_to_seek, seek_offset))

    def tick_loop_trakt(self):
        if plugin_addon.getSetting('trakt_scrobble') != 'true':
            return
        while self.scrobble and self.isPlayingVideo() and self.PlaybackStatus == PlaybackStatus.PLAYING:
            scrobble_trakt(self.ep_id, 1, self.getTime(), self.duration, self.is_movie)
            xbmc.sleep(2500)
        else:
            log('trakt_thread: not playing anything')
            return

    def tick_loop_shoko(self):
        while self.scrobble and self.isPlayingVideo() and self.PlaybackStatus == PlaybackStatus.PLAYING:
            try:
                if plugin_addon.getSetting('file_resume') == 'true' and self.getTime() > 10:
                    nt.sync_offset(self.file_id, self.getTime())
                    xbmc.sleep(2500)
            except:
                pass  # while buffering
        else:
            log('sync_thread: not playing anything')
            return

    def scrobble_finished_episode(self):
        if self.scrobble:
            finished_episode(self.ep_id, self.getTime(), self.duration)
            scrobble_trakt(self.ep_id, 3, self.getTime(), self.duration, self.is_movie)

        if self.is_transcoded:
            nt.get_json(self.path + '/cancel')

        self.Playlist = None
