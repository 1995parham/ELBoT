# In The Name Of God
# ========================================
# [] File Name : lyric.py
#
# [] Creation Date : 26-07-2015
#
# [] Created By : Parham Alvani (parham.alvani@gmail.com)
# =======================================
__author__ = 'Parham Alvani'

import requests
import xml.etree.ElementTree as ET


def lyric(artist, song):
    retval = '404 Not Found'
    response = requests.get('http://api.chartlyrics.com/apiv1.asmx/SearchLyric',
                            params={'artist': artist, 'song': song})
    response = response.text
    results = ET.fromstring(response)
    namespaces = {'master': 'http://api.chartlyrics.com/'}
    for result in results:
        if result.find('master:LyricId', namespaces) is not None and result.find('master:LyricChecksum',
                                                                                 namespaces) is not None:
            lyric_id = result.find('master:LyricId', namespaces).text
            lyric_checksum = result.find('master:LyricChecksum', namespaces).text
            print(lyric_id)
            print(lyric_checksum)
            response = requests.get('http://api.chartlyrics.com/apiv1.asmx/GetLyric',
                                    params={'lyricId': lyric_id, 'lyricCheckSum': lyric_checksum},
                                    headers={'Accept': '*/*', 'Cache-Control': 'no-cache'})
            response = response.text
            print(response)
            retval = ET.fromstring(response).find('master:Lyric', namespaces).text
    return retval


# Just for test :-)
if __name__ == '__main__':
    print(lyric('Chris de Burg', 'The Lady in Red'))
