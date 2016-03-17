# In The Name Of God
# ========================================
# [] File Name : main.py
#
# [] Creation Date : 09-09-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

from elbot.core import BotFather
from elbot.bots.PythonBot import PythonBot
from elbot.bots.LyricBot import LyricBot
from elbot.bots.GoogleBot import GoogleBot
from elbot.bots.YouTubeBot import YouTubeBot

elbot = BotFather.BotFather('128827058:AAHss2FTdF7zKgFudkF7cUlOFbqFC66QO00',
                            [PythonBot(), LyricBot(), GoogleBot(), YouTubeBot()])
elbot.start()
