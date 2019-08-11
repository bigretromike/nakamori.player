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
import json

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
    spam('mark = %s * total (%s) = %s vs current = %s' % (mark, total_time, (total_time*mark), current_time))
    if (total_time * mark) <= current_time:
        _finished = True
        log('Video current_time (%s) has passed watch mark (%s). Marking is as watched!' % (current_time, (total_time*mark)))

    # TODO this got broken for addons in Leia18, until this is somehow fixed we count time by hand (in loop)
    # else:
        # external set position = 1.0 when it want to mark it as watched (based on configuration of external
        # if current_time > 0.0:
        #    _finished = True
        # else:
        #   log('Using an external player, but the settings are set to not mark as watched. Check advancedsettings.xml')

    if _finished:
        if int(ep_id) != 0 and plugin_addon.getSetting('vote_always') == 'true':
            spam('vote_always, voting on episode')
            script_utils.vote_for_episode(ep_id)

        if ep_id != 0:
            from shoko_models.v2 import Episode
            ep = Episode(ep_id, build_full_object=False)
            spam('mark as watched, episode')
            ep.set_watched_status(True)

            # vote on finished series
            if plugin_addon.getSetting('vote_on_series') == 'true':
                from shoko_models.v2 import get_series_for_episode
                series = get_series_for_episode(ep_id)
                # voting should be only when you really watch full series
                spam('vote_on_series, mark: %s / %s' % (series.sizes.watched_episodes, series.sizes.total_episodes))
                if series.sizes.watched_episodes - series.sizes.total_episodes == 0:
                    script_utils.vote_for_series(series.id)

        elif file_id != 0:
            # file watched states
            pass

        # refresh only when we really did watch episode, this way we wait until all action after watching are executed
        script_utils.arbiter(10, 'Container.Refresh')


def direct_play_video(file_id, ep_id=0, mark_as_watched=True, resume=False):
    play_video(file_id, ep_id, mark_as_watched, resume, force_direct_play=True)


def play_video(file_id, ep_id=0, mark_as_watched=True, resume=False, force_direct_play=False):
    """
    Plays a file
    :param file_id: file ID. It is needed to look up the file
    :param ep_id: episode ID, not needed, but it fills in a lot of info
    :param mark_as_watched: should we mark it after playback
    :param resume: should we auto-resume
    :param force_direct_play: force direct play
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
        is_transcoded = False
        m3u8 = ''
        if not force_direct_play:
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
        return player_loop(player, is_transcoded)


def player_loop(player, is_transcoded):
    try:
        monitor = xbmc.Monitor()

        # seek to beggining of stream hack https://github.com/peak3d/inputstream.adaptive/issues/94
        if is_transcoded:
            while not xbmc.Player().isPlayingVideo():
                monitor.waitForAbort(0.25)

            if xbmc.Player().isPlayingVideo():
                xbmc.log("------------------ JSONRPC: seconds seek = " + str(0), xbmc.LOGNOTICE)
                # xbmc.executebuiltin('Seek(0)')
                xbmc.executeJSONRPC(
                    '{"jsonrpc":"2.0","method":"Player.Seek","params":{"playerid":1,"value":{"seconds":0}},"id":1}')

            xbmc.log("------------------ ------------------", xbmc.LOGNOTICE)
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

    is_dash = True
    end_url = eigakan_host + '/api/video/' + str(file_id) + '/end.eigakan'
    if is_dash:
        m3u8_url = eigakan_host + '/api/video/' + str(file_id) + '/play.strm'
        ts_url = eigakan_host + '/api/video/' + str(file_id) + '/chunk-stream0-00004.m4s'
    else:
        m3u8_url = eigakan_host + '/api/video/' + str(file_id) + '/play.m3u8'
        ts_url = eigakan_host + '/api/video/' + str(file_id) + '/play0.ts'

    try:
        eigakan_data = pyproxy.get_json(eigakan_host + '/api/version')
        if eigakan_data is None or 'eigakan' not in eigakan_data:
            # TODO notification that Eigakan is not reachable, with question to disable this setting
            raise RuntimeError('Invalid response from Eigakan')

        if not pyproxy.head(url_in=end_url):

            # please wait, Sending request to Transcode server...
            busy.create(plugin_addon.getLocalizedString(30160), plugin_addon.getLocalizedString(30165))

            audio_stream_id = find_language_index(file_obj.audio_streams, plugin_addon.getSetting('audiolangEigakan'))
            sub_stream_id = find_language_index(file_obj.sub_streams, plugin_addon.getSetting('subEigakan'))

            if audio_stream_id != -1:
                post_data += ',"audio_stream":"' + str(audio_stream_id) + '"'
            if sub_stream_id != -1:
                post_data += ',"subtitles_stream":"' + str(sub_stream_id) + '"'

            if plugin_addon.getSetting('advEigakan') == 'true':
                post_data += ',"resolution":"' + plugin_addon.getSetting('resolutionEigakan') + '"'
                post_data += ',"audio_codec":"' + plugin_addon.getSetting('audioEigakan') + '"'
                post_data += ',"video_bitrate":"' + plugin_addon.getSetting('vbitrateEigakan') + '"'
                post_data += ',"x264_profile":"' + plugin_addon.getSetting('profileEigakan') + '"'
            pyproxy.post_json(video_url, post_data, custom_timeout=0.1)  # non blocking
            xbmc.sleep(1000)
            busy.close()

            # region BUSY Dialog Hell
            try_count = 0
            found = False
            # TODO lang fix
            # please wait,waiting for being queued
            busy.create(plugin_addon.getLocalizedString(30160), "Wiating to be added to queue...")
            while True:
                if busy.iscanceled():
                    break
                ask_for_queue = json.loads(pyproxy.get_json(eigakan_host + '/api/queue/status'))
                if ask_for_queue is None:
                    ask_for_queue = {}
                # {"queue":{"queue":["6330","6330"],"subtitles":{"6330":{"status":"{'init'}"}},"videos":{}}}
                x = ask_for_queue.get('queue', {'queue': ''}).get('queue', [])
                for y in x:
                    if int(y) == int(file_id):
                        found = True
                        break
                if found:
                    break
                try_count += 1
                busy.update(try_count)
                xbmc.sleep(1000)
            busy.close()

            try_count = 0
            found = False
            # TODO lang fix
            # plase wait, waiting for subs to be dumpe
            busy.create(plugin_addon.getLocalizedString(30160), "Dumping subtitles...")
            while True:
                if busy.iscanceled():
                    break
                ask_for_subs = json.loads(pyproxy.get_json(eigakan_host + '/api/queue/%s' % file_id))
                if ask_for_subs is None:
                    ask_for_subs = {}
                #x = ask_for_subs.get('queue', {"subtitles": {}}).get('subtitles', {})
                y = ask_for_subs.get('queue', {"videos": {}}).get('videos', {})
                #for z in x:
                #    if int(z) == int(file_id):
                for k in y:
                    if int(k) == int(file_id):
                        found = True
                        break
                    if found:
                        break
                #    if found:
                #        break
                if found:
                    break
                try_count += 1
                busy.update(try_count)
                xbmc.sleep(1000)
            busy.close()

            try_count = 0
            found = False
            # DO I WANT THIS ? maybe as a buffor ?
            # TODO lang fix
            # please waiti, witiign for starting transcode
            busy.create(plugin_addon.getLocalizedString(30160), "Waiting for transcode to start...")
            while True:
                if busy.iscanceled():
                    break
                ask_for_subs = json.loads(pyproxy.get_json(eigakan_host + '/api/queue/%s' % file_id))
                if ask_for_subs is None:
                    ask_for_subs = {}
                x = ask_for_subs.get('queue', {"videos": {}}).get('videos', {})
                for k in x:
                    if int(k) == int(file_id):
                        percent = x[k].get('percent', 0)
                        if int(percent) > 0:
                            found = True
                            xbmc.log('percent found of transcoding: %s' % percent, xbmc.LOGNOTICE)
                            break
                if found:
                    break
                try_count += 1
                busy.update(try_count)
                xbmc.sleep(1000)
            busy.close()

            try_count = 0
            # please wait, Waiting for response from Server...
            busy.create(plugin_addon.getLocalizedString(30160), plugin_addon.getLocalizedString(30164))
            while True:
                if busy.iscanceled():
                    break
                if pyproxy.head(url_in=ts_url) is False:
                    # x_try = int(plugin_addon.getSetting('tryEigakan'))
                    # if try_count > x_try:
                    #     break
                    try_count += 1
                    busy.update(try_count)
                    xbmc.sleep(1000)
                else:
                    break
            busy.close()

            # endregion

            #postpone_seconds = int(plugin_addon.getSetting('postponeEigakan'))
            #if postpone_seconds > 0:
            #    # please wait, Waiting given time (postpone)
            #    busy.create(plugin_addon.getLocalizedString(30160), plugin_addon.getLocalizedString(30166))
            #    while postpone_seconds > 0:
            #        if busy.iscanceled():
            #            break
            #        xbmc.sleep(1000)
            #        postpone_seconds -= 1
            #        busy.update(postpone_seconds)
            #    busy.close()

        if pyproxy.head(url_in=ts_url):
            is_transcoded = True

    except:
        eh.exception(ErrorPriority.BLOCKING)
        try:
            busy.close()
        except:
            pass

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

    def onPlayBackEnded(self):
        spam('Playback Ended')
        try:
            self.handle_finished_episode()
        except:
            eh.exception(ErrorPriority.HIGH)
        self.PlaybackStatus = PlaybackStatus.ENDED

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
