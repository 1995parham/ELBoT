import urllib.request
import urllib.parse
import json


def google(terms):
    """
    Returns the link and the description of the first result from a google search
    Telegram usage: google <search term>
    :param terms:
    :return:
    """

    query = terms
    print("going to google {}".format(query))
    query = urllib.parse.urlencode({'q': query})
    request = urllib.request.urlopen('http://ajax.googleapis.com/ajax/services/search/web?v=1.0&' + query).read()
    request = str(request)
    response = json.loads(request)
    results = response['responseData']['results']
    return_val = ""
    for result in results:
        title = result['title']
        url = result['url']  # was URL in the original and that threw a name error exception
        title = title.translate({ord(k): None for k in u'<b>'})
        title = title.translate({ord(k): None for k in u'</b>'})
        return_val += title + ' ; ' + url + '\n'

    print("returning %s" % return_val)
    return return_val.encode('utf-8')
