# In The Name Of God
# ========================================
# [] File Name : main.py
#
# [] Creation Date : 09-09-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

from elbot import BotFather
from bots.python_bot import PythonBot

elbot = BotFather.BotFather('128827058:AAHss2FTdF7zKgFudkF7cUlOFbqFC66QO00', [PythonBot()])
elbot.start()