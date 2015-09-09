# In The Name Of God
# ========================================
# [] File Name : Message.py
#
# [] Creation Date : 27-08-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

from . import User
from . import GroupChat

import datetime


class Message:
    """
    This object represents a message.
    :type message_id: int
    :type src: User.User
    :type date: datetime.datetime
    :type chat: User.User or GroupChat.GroupChat
    :type text: str
    """

    def __init__(self, message_id, src, date, chat, text=''):
        self.message_id = message_id
        self.src = src
        self.date = date
        self.text = text
        self.chat = chat


class MessageDictDecoder:
    @staticmethod
    def decode(obj: dict) -> Message:
        text = obj.get('text', '')
        if obj['chat'].get('username', None):
            chat = User.UserDictDecoder.decode(obj['chat'])
        else:
            chat = GroupChat.GroupChatDictDecoder.decode(obj['chat'])
        return Message(message_id=int(obj['message_id']), src=User.UserDictDecoder.decode(obj['from']),
                       date=datetime.datetime.fromtimestamp(obj['date']), chat=chat, text=text)
