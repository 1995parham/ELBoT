python-telegram-bot
===================

This is a telegram bot written in python. It uses the CLI of telegram by vysheng to connect.

## Installation ##

### Install and setup vysheng/tg ###
Install [vysheng's tg cli](http://github.com/vysheng/tg/#installation)

Run that and configure an account
    
    ./telegram -k tg-server.pub
    
### Download ELBOT ###
Clone this repository by doing
    
    git clone https://github.com/asdofindia/python-telegram-bot.git && cd python-telegram-bot
    
### Running python-telegram-bot ###

### Disabling modules ###
If you don't want some of these modules, just remove them from the modules array in callmodule function
If you want to disable twitter, just comment out all lines with 'twitbot' in it in the main function.
If you want to disable robotic replies turn chattybot to False

## Features ##
The features as of now are
  
* can be invoked from within a group or a direct message
* wiki "search terms" will return the first paragraph of the wikipedia article
* google "search terms" will return a few google results
* bot "question" will fetch the answer from wolfram alpha api
* new twitter messages from your feed are automatically sent to the defined peer
* talks random shit based on pandorabots web service thanks to chatterbotapi

## Known Bugs ##

* twitter module repeats tweets. This is more of a problem with the twitter python module rather than this code. Todo: prevent duplicate tweets using timestamp
* chatting with oneself is buggy, I suppose. Not tested very much. No time :P
