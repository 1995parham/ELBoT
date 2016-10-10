#!/usr/bin/env python3

from elbot.core import BotFather
from elbot.bots.PythonBot import PythonBot
from elbot.bots.LyricBot import LyricBot
from elbot.bots.GoogleBot import GoogleBot
from elbot.bots.YouTubeBot import YouTubeBot

elbot = BotFather.BotFather('128827058:AAHss2FTdF7zKgFudkF7cUlOFbqFC66QO00',
                            [PythonBot(), LyricBot(), GoogleBot(), YouTubeBot()])
elbot.start()
