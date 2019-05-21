# -*- coding: utf-8 -*-
import nakamori_utils.shoko_utils
import xbmcgui
from nakamori_utils.globalvars import *
from nakamori_utils import script_utils
from threading import Thread

from proxy.kodi_version_proxy import kodi_proxy
from proxy.python_version_proxy import python_proxy as pyproxy
import error_handler as eh
from error_handler import spam, log, ErrorPriority

busy = xbmcgui.DialogProgress()


class PlaybackStatus(object):
    PLAYING = 'Playing'
    PAUSED = 'Paused'
    STOPPED = 'Stopped'
    ENDED = 'Ended'


eigakan_url = plugin_addon.getSetting('ipEigakan')
eigakan_port = plugin_addon.getSetting('portEigakan')
eigakan_host = 'http://' + eigakan_url + ':' + eigakan_port


def trancode_url(file_id):
    video_url = eigakan_host + '/api/transcode/' + str(file_id)
    return video_url


def scrobble_trakt(ep_id, status, current_time, total_time, movie):
    if plugin_addon.getSetting('trakt_scrobble') == 'true':
        # clamp it to 0-100
        progress = max(0, min(100, int(current_time / total_time * 100.0)))
        nakamori_utils.shoko_utils.trakt_scrobble(ep_id, status, progress, movie, False)


def finished_episode(ep_id, file_id, current_time, total_time):
    _finished = False
    spam('finished_episode > ep_id = %s, file_id = %s, current_time = %s, total_time = %s' % (ep_id, file_id,
                                                                                              current_time, total_time))
    mark = float(plugin_addon.getSetting('watched_mark'))
    if plugin_addon.getSetting('external_player').lower() == 'false':
        pass
    else:
        # mitigate the external player, skipping intro/outro/pv so we cut your setting in half
        mark /= 2
    mark /= 100
    log('mark = %s * total = %s = %s < current = %s' % (mark, total_time, (total_time*mark), current_time))
    if (total_time * mark) <= current_time:
        _finished = True
    # TODO this got broken for addons in Leia18, until this is somehow fixed we count time by hand (in loop)
    # else:
        # external set position = 1.0 when it want to mark it as watched (based on configuration of external
        # if current_time > 0.0:
        #    _finished = True
        # else:
        #   log('Using an external player, but the settings are set to not mark as watched. Check advancedsettings.xml')

    if _finished:
        if int(ep_id) != 0 and plugin_addon.getSetting('vote_always') == 'true':
            script_utils.vote_for_episode(ep_id)
        if ep_id != 0:
            from shoko_models.v2 import Episode
            ep = Episode(ep_id, build_full_object=False)
            ep.set_watched_status(True)
            # TODO we could do vote series here pretty easily
        elif file_id != 0:
            # file watched states
            pass


def play_video(file_id, ep_id=0, mark_as_watched=True, resume=False):
    """
    Plays a file
    :param file_id: file ID. It is needed to look up the file
    :param ep_id: episode ID, not needed, but it fills in a lot of info
    :param mark_as_watched: should we mark it after playback
    :param resume: should we auto-resume
    :return: True if successfully playing
    """

    from shoko_models.v2 import Episode, File, get_series_for_episode

    # check if we're already playing something
    player = xbmc.Player()
    if player.isPlayingVideo():
        playing_item = player.getPlayingFile()
        log('Player is currently playing %s' % playing_item)
        log('Player Stopping')
        player.stop()

    # wait for it to stop
    while True:
        try:
            if not player.isPlayingVideo():
                break
            xbmc.sleep(500)
            continue
        except:
            pass

    # now continue
    file_url = ''

    if int(ep_id) != 0:
        ep = Episode(ep_id, build_full_object=True)
        series = get_series_for_episode(ep_id)
        ep.series_id = series.id
        ep.series_name = series.name
        item = ep.get_listitem()
        f = ep.get_file_with_id(file_id)
    else:
        f = File(file_id, build_full_object=True)
        item = f.get_listitem()

    if item is not None:
        if resume:
            item.resume()
        file_url = f.url_for_player if f is not None else None

    if file_url is not None:
        is_transcoded, m3u8_url = process_transcoder(file_id, file_url, f)

        player = Player()
        player.feed(file_id, ep_id, f.duration, m3u8_url if is_transcoded else file_url, mark_as_watched)

        try:
            if is_transcoded:
                player.play(item=m3u8_url)
            else:
                player.play(item=file_url, listitem=item)

        except:
            eh.exception(ErrorPriority.BLOCKING)

        # leave player alive so we can handle onPlayBackStopped/onPlayBackEnded
        # TODO Move the instance to Service, so that it is never disposed
        xbmc.sleep(int(plugin_addon.getSetting('player_sleep')))
        return player_loop(player)


def player_loop(player):
    # while player.isPlaying():
    #     xbmc.sleep(500)
    try:
        monitor = xbmc.Monitor()
        while player.PlaybackStatus != PlaybackStatus.STOPPED and player.PlaybackStatus != PlaybackStatus.ENDED:
            xbmc.sleep(500)
        if player.PlaybackStatus == PlaybackStatus.STOPPED or player.PlaybackStatus == PlaybackStatus.ENDED:
            log('Playback Ended - Shutting Down: ', monitor.abortRequested())
            return -1
        else:
            log('Playback Ended - Playback status was not "Stopped" or "Ended". It was ', player.PlaybackStatus)
        return 0
    except:
        eh.exception(ErrorPriority.HIGHEST)
        return -1


def process_transcoder(file_id, file_url, file_obj):
    """

    :param file_id:
    :param file_url:
    :type file_url: str
    :param file_obj:
    :type file_obj: File
    :return:
    """
    m3u8_url = ''
    is_transcoded = False
    if plugin_addon.getSetting('enableEigakan') != 'true':
        return is_transcoded, m3u8_url

    video_url = trancode_url(file_id)
    post_data = '"file":"' + file_url + '"'
    try_count = 0
    m3u8_url = eigakan_host + '/api/video/' + str(file_id) + '/play.m3u8'
    ts_url = eigakan_host + '/api/video/' + str(file_id) + '/play0.ts'

    try:
        eigakan_data = pyproxy.get_json(eigakan_host + '/api/version')
        if eigakan_data is None or 'eigakan' not in eigakan_data:
            raise RuntimeError('Invalid response from Eigakan')

        audio_stream_id = find_language_index(file_obj.audio_streams, plugin_addon.getSetting('audiolangEigakan'))
        sub_stream_id = find_language_index(file_obj.sub_streams, plugin_addon.getSetting('subEigakan'))

        busy.create(plugin_addon.getLocalizedString(30160), plugin_addon.getLocalizedString(30165))

        if audio_stream_id != -1:
            post_data += ',"audio_stream":"' + str(audio_stream_id) + '"'
        if sub_stream_id != -1:
            post_data += ',"subtitles_stream":"' + str(sub_stream_id) + '"'

        if plugin_addon.getSetting('advEigakan') == 'true':
            post_data += ',"resolution":"' + plugin_addon.getSetting('resolutionEigakan') + '"'
            post_data += ',"audio_codec":"' + plugin_addon.getSetting('audioEigakan') + '"'
            post_data += ',"video_bitrate":"' + plugin_addon.getSetting('vbitrateEigakan') + '"'
            post_data += ',"x264_profile":"' + plugin_addon.getSetting('profileEigakan') + '"'
        pyproxy.post_json(video_url, post_data)
        xbmc.sleep(1000)
        busy.close()

        busy.create(plugin_addon.getLocalizedString(30160), plugin_addon.getLocalizedString(30164))
        while True:
            if pyproxy.head(url_in=ts_url) is False:
                x_try = int(plugin_addon.getSetting('tryEigakan'))
                if try_count > x_try:
                    break
                if busy.iscanceled():
                    break
                try_count += 1
                busy.update(try_count)
                xbmc.sleep(1000)
            else:
                break
        busy.close()

        postpone_seconds = int(plugin_addon.getSetting('postponeEigakan'))
        if postpone_seconds > 0:
            busy.create(plugin_addon.getLocalizedString(30160), plugin_addon.getLocalizedString(30166))
            while postpone_seconds > 0:
                xbmc.sleep(1000)
                postpone_seconds -= 1
                busy.update(postpone_seconds)
                if busy.iscanceled():
                    break
            busy.close()

        if pyproxy.head(url_in=ts_url):
            is_transcoded = True

    except:
        eh.exception(ErrorPriority.BLOCKING)
        busy.close()

    return is_transcoded, m3u8_url


def find_language_index(streams, setting):
    stream_index = -1
    stream_id = -1
    for code in setting.split(','):
        for stream in streams:
            stream_index += 1
            if code in streams[stream].get('Language', '').lower() != '':
                stream_id = stream_index
                break
            if code in streams[stream].get('LanguageCode', '').lower() != '':
                stream_id = stream_index
                break
            if code in streams[stream].get('Title', '').lower() != '':
                stream_id = stream_index
                break
        if stream_id != -1:
            break
    return stream_id


# noinspection PyUnusedFunction
class Player(xbmc.Player):
    def __init__(self):
        spam('Player Initialized')
        xbmc.Player.__init__(self)
        self._t = None  # trakt thread
        self._s = None  # shoko thread
        self._u = None  # update thread
        self._details = None
        self.Playlist = None
        self.PlaybackStatus = 'Stopped'
        self.LoopStatus = 'None'
        self.Shuffle = False
        self.is_transcoded = False
        self.is_movie = None
        self.file_id = 0
        self.ep_id = 0
        # we will store duration and time in kodi format here, so that calls to the player will match
        self.duration = 0
        self.time = 0
        self.path = ''
        self.scrobble = True
        self.is_external = False

        self.CanControl = True

    def reset(self):
        spam('Player reset')
        self.__init__()

    def feed(self, file_id, ep_id, duration, path, scrobble):
        spam('Player feed - file_id=%s ep_id=%s duration=%s path=%s scrobble=%s' %
             (file_id, ep_id, duration, path, scrobble))
        self.file_id = file_id
        self.ep_id = ep_id
        self.duration = kodi_proxy.duration_to_kodi(duration)
        self.path = path
        self.scrobble = scrobble

    def onAVStarted(self):
        # Will be called when Kodi has a video or audiostream.
        spam('onAVStarted')

        # isExternalPlayer() ONLY works when isPlaying(), other than that it throw 0 always
        # setting it before results in false setting
        try:
            is_external = str(kodi_proxy.external_player(self)).lower()
            plugin_addon.setSetting(id='external_player', value=is_external)
        except:
            eh.exception(ErrorPriority.HIGH)
        spam(self)

        if kodi_proxy.external_player(self):
            log('Using External Player')
            self.is_external = True

    def onAVChange(self):
        # Will be called when Kodi has a video, audio or subtitle stream. Also happens when the stream changes.
        spam('onAVChange')

    def onPlayBackStarted(self):
        spam('Playback Started')
        try:
            if plugin_addon.getSetting('enableEigakan') == 'true':
                log('Player is set to use Transcoding')
                self.is_transcoded = True

            # wait until the player is init'd and playing
            self.set_duration()

            self.PlaybackStatus = PlaybackStatus.PLAYING
            # we are making the player global, so if a stop is issued, then Playing will change
            while not self.isPlaying() and self.PlaybackStatus == PlaybackStatus.PLAYING:
                xbmc.sleep(100)
            if self.PlaybackStatus != PlaybackStatus.PLAYING:
                return

            # TODO get series and populate
            self.is_movie = False
            if self.duration > 0 and self.scrobble:
                scrobble_trakt(self.ep_id, 1, self.getTime(), self.duration, self.is_movie)

            self.start_loops()
        except:
            eh.exception(ErrorPriority.HIGHEST)

    def onPlayBackResumed(self):
        spam('Playback Resumed')
        self.PlaybackStatus = PlaybackStatus.PLAYING
        try:
            self.start_loops()
        except:
            eh.exception(ErrorPriority.HIGH)

    def start_loops(self):
        try:
            self._t.stop()
        except:
            pass
        self._t = Thread(target=self.tick_loop_trakt, args=())
        self._t.daemon = True
        self._t.start()

        try:
            self._s.stop()
        except:
            pass
        self._s = Thread(target=self.tick_loop_shoko, args=())
        self._s.daemon = True
        self._s.start()

        try:
            self._u.stop()
        except:
            pass
        self._u = Thread(target=self.tick_loop_update_time, args=())
        self._u.daemon = True
        self._u.start()

    def onPlayBackStopped(self):
        spam('Playback Stopped')
        try:
            self.handle_finished_episode()
        except:
            eh.exception(ErrorPriority.HIGH)
        self.PlaybackStatus = PlaybackStatus.STOPPED
        self.refresh()

    def onPlayBackEnded(self):
        spam('Playback Ended')
        try:
            self.handle_finished_episode()
        except:
            eh.exception(ErrorPriority.HIGH)
        self.PlaybackStatus = PlaybackStatus.ENDED
        self.refresh()

    def onPlayBackPaused(self):
        spam('Playback Paused')
        self.PlaybackStatus = PlaybackStatus.PAUSED
        self.scrobble_time()

    def onPlayBackSeek(self, time_to_seek, seek_offset):
        log('Playback Paused - time_to_seek=%s seek_offset=%s' % (time_to_seek, seek_offset))
        self.time = self.getTime()
        self.scrobble_time()

    def set_duration(self):
        if self.duration != 0:
            return
        duration = int(self.getTotalTime())
        if self.is_transcoded:
            duration = self.duration
        self.duration = duration

    def scrobble_time(self):
        if not self.scrobble:
            return
        try:
            scrobble_trakt(self.ep_id, 2, self.time, self.duration, self.is_movie)
            if plugin_addon.getSetting('file_resume') == 'true' and self.time > 10:
                from shoko_models.v2 import File
                f = File(self.file_id)
                f.set_resume_time(kodi_proxy.duration_from_kodi(self.time))
        except:
            eh.exception(ErrorPriority.HIGH)

    def tick_loop_trakt(self):
        if plugin_addon.getSetting('trakt_scrobble') != 'true':
            return
        while self.scrobble and self.isPlayingVideo() and self.PlaybackStatus == PlaybackStatus.PLAYING:
            scrobble_trakt(self.ep_id, 1, self.time, self.duration, self.is_movie)
            xbmc.sleep(2500)
        else:
            log('trakt_thread: not playing anything')
            return

    def tick_loop_shoko(self):
        while self.scrobble and self.isPlayingVideo() and self.PlaybackStatus == PlaybackStatus.PLAYING:
            try:
                if plugin_addon.getSetting('file_resume') == 'true' and self.time > 10:
                    from shoko_models.v2 import File
                    f = File(self.file_id)
                    f.set_resume_time(kodi_proxy.duration_from_kodi(self.time))
                    xbmc.sleep(2500)
            except:
                pass  # while buffering
        else:
            log('sync_thread: not playing anything')
            return

    def tick_loop_update_time(self):
        try:
            while self.isPlayingVideo() and self.PlaybackStatus == PlaybackStatus.PLAYING:
                try:
                    # Leia seems to have a bug where calling self.getTotalTime() fails at times
                    # Try until it succeeds
                    self.set_duration()

                    if not self.is_external:
                        self.time = self.getTime()
                    else:
                        self.time += 0.5
                        # log('--------------> time is %s ' % self.getTime())

                    xbmc.sleep(500)
                except:
                    pass  # while buffering
        except:
            eh.exception(ErrorPriority.HIGHEST)

    def handle_finished_episode(self):
        if self.scrobble:
            scrobble_trakt(self.ep_id, 3, self.time, self.duration, self.is_movie)

        finished_episode(self.ep_id, self.file_id, self.time, self.duration)

        if self.is_transcoded:
            pyproxy.get_json(trancode_url(self.file_id) + '/cancel')

        self.Playlist = None

    def refresh(self):
        script_utils.arbiter(10, 'Container.Refresh')
