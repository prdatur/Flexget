from __future__ import unicode_literals, division, absolute_import
import urlparse
import logging
import urllib
import zlib
import re
from flexget.entry import Entry
from flexget.plugin import register_plugin, internet, PluginError
from flexget.utils.soup import get_soup
from flexget.utils.cached_input import cached

log = logging.getLogger('html')


class InputHtml(object):
    """
        Parses urls from html page. Usefull on sites which have direct download
        links of any type (mp3, jpg, torrent, ...).

        Many anime-fansubbers do not provide RSS-feed, this works well in many cases.

        Configuration expects url parameter.

        Note: This returns ALL links on url so you need to configure filters
        to match only to desired content.
    """

    def validator(self):
        from flexget import validator
        root = validator.factory()
        root.accept('text')
        advanced = root.accept('dict')
        advanced.accept('url', key='url', required=True)
        advanced.accept('text', key='username')
        advanced.accept('text', key='password')
        advanced.accept('text', key='dump')
        advanced.accept('text', key='title_from')
        regexps = advanced.accept('list', key='links_re')
        regexps.accept('regexp')
        return root

    def build_config(self, config):

        def get_auth_from_url():
            """Moves basic authentication from url to username and password fields"""
            parts = list(urlparse.urlsplit(config['url']))
            split = parts[1].split('@')
            if len(split) > 1:
                auth = split[0].split(':')
                if len(auth) == 2:
                    config['username'], config['password'] = auth[0], auth[1]
                else:
                    log.warning('Invalid basic authentication in url: %s' % config['url'])
                parts[1] = split[1]
                config['url'] = urlparse.urlunsplit(parts)

        if isinstance(config, basestring):
            config = {'url': config}
        get_auth_from_url()
        return config

    @cached('html')
    @internet(log)
    def on_task_input(self, task, config):
        config = self.build_config(config)

        log.debug('InputPlugin html requesting url %s' % config['url'])

        auth = None
        if config.get('username') and config.get('password'):
            log.debug('Basic auth enabled. User: %s Password: %s' % (config['username'], config['password']))
            auth = (config['username'], config['password'])

        page = task.requests.get(config['url'], auth=auth)
        soup = get_soup(page.text)

        # dump received content into a file
        if 'dump' in config:
            name = config['dump']
            log.info('Dumping %s into %s' % (config['url'], name))
            data = soup.prettify()
            f = open(name, 'w')
            f.write(data)
            f.close()

        return self.create_entries(config['url'], soup, config)

    def _title_from_link(self, link, log_link):
        title = link.text
        # longshot from next element (?)
        if not title:
            title = link.next.string
            if title is None:
                log.debug('longshot failed for %s' % log_link)
                return None
        return title or None

    def _title_from_url(self, url):
        parts = urllib.splitquery(url[url.rfind('/') + 1:])
        title = urllib.unquote_plus(parts[0])
        return title

    def create_entries(self, page_url, soup, config):

        queue = []
        duplicates = {}
        duplicate_limit = 4

        def title_exists(title):
            """Helper method. Return True if title is already added to entries"""
            for entry in queue:
                if entry['title'] == title:
                    return True

        for link in soup.find_all('a'):
            # not a valid link
            if not link.has_attr('href'):
                continue
            # no content in the link
            if not link.contents:
                continue

            url = link['href']
            log_link = url
            log_link = log_link.replace('\n', '')
            log_link = log_link.replace('\r', '')

            # fix broken urls
            if url.startswith('//'):
                url = 'http:' + url
            elif not url.startswith('http://') or not url.startswith('https://'):
                url = urlparse.urljoin(page_url, url)

            # get only links matching regexp
            regexps = config.get('links_re', None)
            if regexps:
                accept = False
                for regexp in regexps:
                    if re.search(regexp, url):
                        accept = True
                if not accept:
                    continue

            title_from = config.get('title_from', 'auto')
            if title_from == 'url':
                title = self._title_from_url(url)
                log.debug('title from url: %s' % title)
            elif title_from == 'title':
                if not link.has_attr('title'):
                    log.warning('Link `%s` doesn\'t have title attribute, ignored.' % log_link)
                    continue
                title = link['title']
                log.debug('title from title: %s' % title)
            elif title_from == 'auto':
                title = self._title_from_link(link, log_link)
                if title is None:
                    continue
                # automatic mode, check if title is unique
                # if there are too many duplicate titles, switch to title_from: url
                if title_exists(title):
                    # ignore index links as a counter
                    if 'index' in title and len(title) < 10:
                        log.debug('ignored index title %s' % title)
                        continue
                    duplicates.setdefault(title, 0)
                    duplicates[title] += 1
                    if duplicates[title] > duplicate_limit:
                        # if from url seems to be bad choice use title
                        from_url = self._title_from_url(url)
                        switch_to = 'url'
                        for ext in ('.html', '.php'):
                            if from_url.endswith(ext):
                                switch_to = 'title'
                        log.info('Link names seem to be useless, auto-configuring \'title_from: %s\'. '
                                 'This may not work well, you might need to configure it yourself.' % switch_to)
                        config['title_from'] = switch_to
                        # start from the beginning  ...
                        return self.create_entries(page_url, soup, config)
            elif title_from == 'link' or title_from == 'contents':
                # link from link name
                title = self._title_from_link(link, log_link)
                if title is None:
                    continue
                log.debug('title from link: %s' % title)
            else:
                raise PluginError('Unknown title_from value %s' % title_from)

            if not title:
                log.debug('title could not be determined for %s' % log_link)
                continue

            # strip unicode white spaces
            title = title.replace(u'\u200B', u'').strip()

            # in case the title contains xxxxxxx.torrent - foooo.torrent clean it a bit (get up to first .torrent)
            # TODO: hack
            if title.lower().find('.torrent') > 0:
                title = title[:title.lower().find('.torrent')]

            if title_exists(title):
                # title link should be unique, add CRC32 to end if it's not
                hash = zlib.crc32(url.encode("utf-8"))
                crc32 = '%08X' % (hash & 0xFFFFFFFF)
                title = '%s [%s]' % (title, crc32)
                # truly duplicate, title + url crc already exists in queue
                if title_exists(title):
                    continue
                log.debug('uniqued title to %s' % title)

            entry = Entry()
            entry['url'] = url
            entry['title'] = title

            queue.append(entry)

        # add from queue to task
        return queue


register_plugin(InputHtml, 'html', api_ver=2)
