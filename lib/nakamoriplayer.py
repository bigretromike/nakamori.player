# -*- coding: utf-8 -*-
import xbmc
import time
import nakamoritools as nt

Playback_Status = ["Playing", "Paused", "Stopped"]


def log(msg):
    xbmc.log("nakamori.player::%s" % msg, level=xbmc.LOGNOTICE)


def sync_resume(file_id, current_time):
    if nt.addon.getSetting("syncwatched") == "true":
        nt.sync_offset(file_id, current_time)


def trakt(ep_id, status, current_time, total_time, movie):
    if nt.addon.getSetting("trakt_scrobble") == "true":
        notification = False
        if nt.addon.getSetting("trakt_scrobble_notification") == "true":
            notification = True
        progress = int((current_time / total_time) * 100)
        nt.trakt_scrobble(ep_id, status, progress, movie, notification)


def did_i_watch_entire_episode(current_time, total_time, ep_id, user_rate):
    mark = float(nt.addon.getSetting("watched_mark"))
    mark /= 100
    if (total_time * mark) < current_time:
        file_fin = True  # po co

        if nt.addon.getSetting('vote_always') == 'true':
            # convert in case shoko give float
            if user_rate == '0.0':
                nt.vote_episode(ep_id)
            else:
                xbmc.log("------- vote_always found 'userrate':" + str(user_rate),
                         xbmc.LOGNOTICE)


class Service(xbmc.Player):
    def __init__(self):
        log('Init')
        xbmc.Player.__init__(self)
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
            'shoko:userrate': '0.0'
        }
        self.CanControl = True
        log('finish init')
        if self.isPlaying():
            self.onPlayBackStarted()

    def feed(self, details):
        log('feed')
        self._details = details

    # called when kodi starts playing a file
    def onPlayBackStarted(self):
        log('onPlaybackStarted')

        # wait a second
        if not xbmc.abortRequested:
            time.sleep(1)
            if not self.isPlayingVideo():
                return

        if nt.addon.getSetting('enableEigakan'):
            self.Transcoded = True

        self.Metadata['shoko:current'] = 0
        self.Metadata['shoko:duration'] = self._details['duration']
        self.Metadata['shoko:epid'] = self._details['epid']
        self.Metadata['shoko:movie'] = self._details['movie']
        self.Metadata['shoko:fileid'] = self._details['fileid']

        self.PlaybackStatus = 'Playing'
        duration = self.getTotalTime()
        if self.Transcoded:
            duration = self.Metadata.get('shoko:duration')
        trakt(self.Metadata.get('shoko:epid'), 1, 0, duration, self.Metadata.get('shoko:movie'))

        self.onPlayBackResumed()

    def onPlayBackStopped(self):
        self.onPlayBackEnded()

    def onPlayBackEnded(self):
        # TODO userrate
        did_i_watch_entire_episode(self.Metadata.get('shoko:current'), self.Metadata.get('shoko:duration'),
                                   self.Metadata.get('shoko:epid'), '0.0')
        trakt(self.Metadata.get('shoko:epid'), 3, self.Metadata.get('shoko:current'),
              self.Metadata.get('shoko:duration'), self.Metadata.get('shoko:movie'))
        self.Playlist = None
        self.PlaybackStatus = 'Stopped'

    def onPlayBackPaused(self):
        trakt(self.Metadata.get('shoko:epid'), 2, self.Metadata.get('shoko:current'),
              self.Metadata.get('shoko:duration'), self.Metadata.get('shoko:movie'))
        self.PlaybackStatus = 'Paused'

    def onPlayBackResumed(self):
        self.PlaybackStatus = 'Playing'
        while self.isPlayingVideo():
            self.Metadata['shoko:current'] = self.getTime()
            trakt(self.Metadata.get('shoko:epid'), 1, self.getTime(),
                  self.Metadata.get('shoko:duration'), self.Metadata.get('shoko:movie'))
            time.sleep(3)
        else:
            log("not playing anything")
