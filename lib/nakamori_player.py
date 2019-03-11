# -*- coding: utf-8 -*-
import json

import xbmcgui
from nakamori_utils import nakamoritools as nt, model_utils, infolabel_utils, kodi_utils
from nakamori_utils.globalvars import *
from threading import Thread

from proxy.kodi_version_proxy import kodi_proxy
from proxy.python_version_proxy import python_proxy as pyproxy
from error_handler import log

busy = xbmcgui.DialogProgress()


class PlaybackStatus(object):
    PLAYING = 'Playing'
    PAUSED = 'Paused'
    STOPPED = 'Stopped'
    ENDED = 'Ended'


def scrobble_trakt(ep_id, status, current_time, total_time, movie):
    if plugin_addon.getSetting('trakt_scrobble') == 'true':
        progress = int(current_time / total_time * 100.0)
        nt.trakt_scrobble(ep_id, status, progress, movie, False)


def finished_episode(ep_id, current_time, total_time):
    _finished = False
    if plugin_addon.getSetting('external_player').lower() == 'false':
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
        from shoko_models.v2 import Episode
        ep = Episode(ep_id, build_full_object=False)
        ep.set_watched_status(True)
    else:
        # TODO unsort files vote/watchmark support
        log('mark = watched but it was unsort file')


def play_video(file_id, ep_id=0, mark_as_watched=True, resume=False):
    """
    Plays a file
    :param file_id: file ID. It is needed to look up the file
    :param ep_id: episode ID, not needed, but it fills in a lot of info
    :param resume: should we auto-resume
    :return: True if successfully playing
    """

    from shoko_models.v2 import Episode, File
    file_url = ''
    item = ''

    if int(ep_id) != 0:
        ep = Episode(ep_id, build_full_object=True)
        f = ep.get_file_with_id(file_id)
        details = infolabel_utils.get_infolabels_for_episode(ep)
    else:
        f = File(file_id, build_full_object=True)
        details = infolabel_utils.get_infolabels_for_file(f)

    if f is not None:
        item = f.get_listitem()
        if resume:
            item.set_resume()
        file_url = f.url_for_player

    is_transcoded, m3u8_url = process_transcoder(file_id, file_url, f)

    player = Player()
    player.feed(file_id, ep_id, details.get('duration', 0), m3u8_url if is_transcoded else file_url, mark_as_watched)

    try:
        if is_transcoded:
            player.play(item=m3u8_url)
        else:
            player.play(item=file_url, listitem=item)

    except Exception as player_ex:
        xbmc.log('---> player_ex: ' + str(player_ex), xbmc.LOGWARNING)

    # leave player alive so we can handle onPlayBackStopped/onPlayBackEnded
    xbmc.sleep(int(plugin_addon.getSetting('player_sleep')))

    return player_loop(player)


def player_loop(player):
    # while player.isPlaying():
    #     xbmc.sleep(500)
    monitor = xbmc.Monitor()
    while player.PlaybackStatus != 'Stopped' and player.PlaybackStatus != 'Ended':
        xbmc.sleep(500)
    if player.PlaybackStatus == 'Ended':
        xbmc.log(' Ended -------~ ~~ ~ ----> ' + str(monitor.abortRequested()), xbmc.LOGWARNING)
        return -1
    else:
        xbmc.log('player.PlaybackStatus=============' + str(player.PlaybackStatus))
    xbmc.log('-------~ ~~ ~ ----> ' + str(monitor.abortRequested()), xbmc.LOGWARNING)
    return 0


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
    eigakan_url = plugin_addon.getSetting('ipEigakan')
    eigakan_port = plugin_addon.getSetting('portEigakan')
    eigakan_host = 'http://' + eigakan_url + ':' + eigakan_port
    video_url = eigakan_host + '/api/transcode/' + str(file_id)
    post_data = '"file":"' + file_url + '"'
    try_count = 0
    m3u8_url = eigakan_host + '/api/video/' + str(file_id) + '/play.m3u8'
    ts_url = eigakan_host + '/api/video/' + str(file_id) + '/play0.ts'

    try:
        eigakan_data = nt.get_json(eigakan_host + '/api/version')
        if 'eigakan' not in eigakan_data:
            nt.error('Eigakan server is unavailable')
            return is_transcoded, m3u8_url

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

    except Exception as exc:
        nt.error('eigakan.post_json nt.error', str(exc))
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


def play_video_old(ep_id, raw_id, movie):
    """
    Plays a file or episode
    Args:
        ep_id: episode id, if applicable for watched status and stream details
        raw_id: file id, that is only used when ep_id = 0
        movie: determinate if played object is movie or episode (ex.Trakt)
    Returns:

    """
    details = {
        'plot':          xbmc.getInfoLabel('ListItem.Plot'),
        'title':         xbmc.getInfoLabel('ListItem.Title'),
        'sorttitle':     xbmc.getInfoLabel('ListItem.Title'),
        'rating':        xbmc.getInfoLabel('ListItem.Rating'),
        'duration':      xbmc.getInfoLabel('ListItem.Duration'),
        'mpaa':          xbmc.getInfoLabel('ListItem.Mpaa'),
        'year':          xbmc.getInfoLabel('ListItem.Year'),
        'tagline':       xbmc.getInfoLabel('ListItem.Tagline'),
        'episode':       xbmc.getInfoLabel('ListItem.Episode'),
        'aired':         xbmc.getInfoLabel('ListItem.Premiered'),
        'tvshowtitle':   xbmc.getInfoLabel('ListItem.TVShowTitle'),
        'votes':         xbmc.getInfoLabel('ListItem.Votes'),
        'originaltitle': xbmc.getInfoLabel('ListItem.OriginalTitle'),
        'size':          xbmc.getInfoLabel('ListItem.Size'),
        'season':        xbmc.getInfoLabel('ListItem.Season'),
        'epid':          ep_id,  # player
        'movie':         movie,  # player
        'fileid':        0,      # player, coded below
        'rawid':         raw_id  # player
    }

    file_id = ''
    file_url = ''
    file_body = None
    item = ''

    resume = plugin_addon.getSetting('resume') == '1'
    plugin_addon.setSetting('resume', '0')

    try:
        if ep_id != '0':
            episode_url = server + '/api/ep?id=' + str(ep_id)
            episode_url = pyproxy.set_parameter(episode_url, 'level', '1')
            html = nt.get_json(pyproxy.encode(episode_url))
            if plugin_addon.getSetting('spamLog') == 'true':
                xbmc.log(html, xbmc.LOGWARNING)
            episode_body = json.loads(html)
            if plugin_addon.getSetting('pick_file') == 'true':
                file_id = kodi_utils.file_list_gui(episode_body)
            else:
                file_id = episode_body['files'][0]['id']
        else:
            file_id = raw_id

        if file_id is not None and file_id != 0:
            details['fileid'] = file_id
            file_url = server + '/api/file?id=' + str(file_id)
            file_body = json.loads(nt.get_json(file_url))

            file_url = file_body['url']
            server_path = file_body.get('server_path', '')
            if server_path is not None and server_path != '':
                try:
                    if os.path.isfile(server_path):
                        if server_path.startswith(u'\\\\'):
                            server_path = u'smb:'+server_path
                        file_url = server_path
                except:
                    pass

            # Information about streams inside video file
            # Video
            codecs = dict()
            model_utils.video_file_information(file_body['media'], codecs)
            details['path'] = file_url
            details['duration'] = file_body.get('duration', 0)
            details['size'] = file_body['size']

            item = xbmcgui.ListItem(details.get('title', 'Unknown'),
                                    thumbnailImage=xbmc.getInfoLabel('ListItem.Thumb'),
                                    path=file_url)
            item.setInfo(type='Video', infoLabels=details)

            if 'offset' in file_body:
                offset = file_body.get('offset', 0)
                if offset != 0:
                    offset = int(offset) / 1000
                    if plugin_addon.getSetting('file_resume') == 'true' and resume:
                        item.setProperty('ResumeTime', str(offset))
                        item.setProperty('StartOffset', str(offset))

            for stream_index in codecs['VideoStreams']:
                if not isinstance(codecs['VideoStreams'][stream_index], dict):
                    continue
                item.addStreamInfo('video', codecs['VideoStreams'][stream_index])
            for stream_index in codecs['AudioStreams']:
                if not isinstance(codecs['AudioStreams'][stream_index], dict):
                    continue
                item.addStreamInfo('audio', codecs['AudioStreams'][stream_index])
            for stream_index in codecs['SubStreams']:
                if not isinstance(codecs['SubStreams'][stream_index], dict):
                    continue
                item.addStreamInfo('subtitle', codecs['SubStreams'][stream_index])
        else:
            if plugin_addon.getSetting('pick_file') == 'false':
                nt.error('file_id not retrieved')
            return 0
    except Exception as exc:
        nt.error('util.nt.error getting episode info', str(exc))

    is_transcoded, m3u8_url = process_transcoder(file_id, file_url)

    player = Player()
    player.feed(file_id, ep_id, details.get('duration', 0), m3u8_url if is_transcoded else file_url, True)

    try:
        if is_transcoded:
            details['path'] = m3u8_url
            player.feed(file_id, ep_id, details.get('duration', 0), m3u8_url if is_transcoded else file_url, True)
            player.play(item=m3u8_url)
        else:
            player.play(item=file_url, listitem=item)

    except Exception as player_ex:
        xbmc.log('---> player_ex: ' + str(player_ex), xbmc.LOGWARNING)

    # leave player alive so we can handle onPlayBackStopped/onPlayBackEnded
    xbmc.sleep(int(plugin_addon.getSetting('player_sleep')))

    return player_loop(player)


# noinspection PyUnusedFunction
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
        self.time = 0
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

        self._s = Thread(target=self.tick_loop_update_time, args=())
        self._s.daemon = True
        self._s.start()

    def onPlayBackStopped(self):
        log('onPlayBackStopped')
        self.scrobble_finished_episode()
        self.PlaybackStatus = PlaybackStatus.STOPPED

    def onPlayBackEnded(self):
        log('onPlayBackEnded')
        # TODO userrate support
        self.scrobble_finished_episode()
        self.PlaybackStatus = PlaybackStatus.ENDED

    def onPlayBackPaused(self):
        log('onPlayBackPaused')
        self.PlaybackStatus = PlaybackStatus.PAUSED
        scrobble_trakt(self.ep_id, 2, self.time, self.duration, self.is_movie)
        if plugin_addon.getSetting('file_resume') == 'true' and self.time > 10:
            nt.sync_offset(self.file_id, self.time)

    def onPlayBackSeek(self, time_to_seek, seek_offset):
        log('onPlayBackSeek with %s, %s' % (time_to_seek, seek_offset))
        self.time = self.getTime()
        if plugin_addon.getSetting('file_resume') == 'true' and self.time > 10:
            nt.sync_offset(self.file_id, self.time)

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
                    nt.sync_offset(self.file_id, self.time)
                    xbmc.sleep(2500)
            except:
                pass  # while buffering
        else:
            log('sync_thread: not playing anything')
            return

    def tick_loop_update_time(self):
        while self.isPlayingVideo() and self.PlaybackStatus == PlaybackStatus.PLAYING:
            try:
                self.time = self.getTime()
                xbmc.sleep(500)
            except:
                pass  # while buffering
        else:
            log('update_time: not playing anything')
            return

    def scrobble_finished_episode(self):
        if self.scrobble:
            finished_episode(self.ep_id, self.time, self.duration)
            scrobble_trakt(self.ep_id, 3, self.time, self.duration, self.is_movie)

        if self.is_transcoded:
            nt.get_json(self.path + '/cancel')

        self.Playlist = None
