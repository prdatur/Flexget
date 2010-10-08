import logging
from flexget.plugin import *

log = logging.getLogger("btchat")


class UrlRewriteBtChat:
    """BtChat urlrewriter."""

    def url_rewritable(self, feed, entry):
        return entry['url'].startswith('http://www.bt-chat.com/download.php')
        
    def url_rewrite(self, feed, entry):
        entry['url'] = entry['url'].replace('download.php', 'download1.php')

register_plugin(UrlRewriteBtChat, 'btchat', groups=['urlrewriter'])
