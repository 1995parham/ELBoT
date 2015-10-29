# In The Name Of God
# ========================================
# [] File Name : YouTubeBot.py
#
# [] Creation Date : 10/29/15
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

from elbot.core.AbstractBot import AbstractBot
from elbot.core.Message import Message
from elbot.core.BotFather import BotFather
from pytube import YouTube
import os


class YouTubeBot(AbstractBot):
    bot_name = 'ytdl'

    def run_query(self, message: Message, father: BotFather):
        father.send_chat_action(message.chat.id, 'upload_video')
        for (path, name) in self.youtube_download(message.text):
            father.send_video(video=path, chat_id=message.chat.id,
                              caption=name)
            father.send_chat_action(message.chat.id, 'upload_video')

    @staticmethod
    def youtube_download(video_ids):
        video_ids = video_ids.split('\n')
        for video_id in video_ids:
            yt = YouTube('https://www.youtube.com/watch?v=' + video_id)
            video = yt.get('mp4')
            name = yt.filename
            if not os.path.isfile('res/' + name + 'mp4'):
                video.download('res/')
            yield ('res/' + name + '.mp4', name)
