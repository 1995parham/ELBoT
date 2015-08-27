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
    """

    def __init__(self, message_id, src, date, chat):
        self.message_id = message_id
        self.src = src
        self.date = date
        self.chat = chat
