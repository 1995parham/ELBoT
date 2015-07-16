# In The Name Of God
# ========================================
# [] File Name : base_bot.py
#
# [] Creation Date : 27-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import bot


class BaseBot(type):
    def __init__(cls, name, bases, attrs):
        bot.MainBot.modules.append(cls)
        attrs['bot_name'] = name
