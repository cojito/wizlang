import urllib2
import json
import string
import re
import httplib
import urlparse
import sets
import cPickle
import os.path
import time
import multiprocessing
import traceback
import socket
from multiprocessing import Manager
from sets import Set
import numexpr as ne
from wiki import *
from utils import *
import veclib

backend_url_nearest = r'http://localhost:5005/nearest/'
#backend_url_nearest = r'http://thisplusthat.me:5005/nearest/'
backend_url_farthest = r'http://localhost:5005/farthest/'
#backend_url_farthest = r'http://thisplusthat.me:5005/farthest/'

def eval_sign(query):
    """ This is a dumb parser that assign + or - to every character
        in an expression. We can then use this to lookup the sign of
        every token in the expression"""
    out = ""
    sign = '+' # defailt is positive
    for c in query:
        if c == '-': 
            sign = '-'
        elif c == '+':
            sign = '+'
        out += sign
    return out

def prettify(phrase):
    phrase = phrase.replace('_', ' ')
    phrase = phrase.replace('  ',' ')
    phrase = phrase.replace('  ',' ')
    phrase = phrase.replace('  ',' ')
    phrase = phrase.replace('  ',' ')
    text = ''
    for word in phrase.split(' '):
        try:
            word = word[0].upper() + word[1:]
        except:
            pass
        text += word + ' '
    return text

def countdig(word):
    return sum([w.isdigit() for w in word])

class Actor(object):
    """ This encapsulates all of the actions associated with a results 
        page. We test multiple Actor objects until validate(query) is True
        and then parse and evaluate the query, which is usually called 
        through run"""
    name = 'Actor'

    def validate(self, query):
        """Is the given query suitable for this Action"""
        return False

    def parse(self, query):
        """ Reduce the query into arguments for evaluate"""
        return 

    def evaluate(self, arg, **kwargs):
        """Evaluate the query and return a results object
           that gets plugged into the Jinja code in results.html.
           Defaults to a pass-through to OMDB"""
        return {}

    def run(self, query):
        start = time.time()
        if False:
            try:
                args, kwargs = self.parse(query)
                reps = self.evaluate(*args, **kwargs)
            except:
                traceback.print_exc()
                reps = {}
        else:
            args = self.parse(query)
            reps = self.evaluate(*args)
        reps['actor'] = self.name
        stop = time.time()
        reps['query_time'] = "%1.1f" %(stop - start)
        return reps


@timer
@persist_to_file
def result_chain(canonical):
    """Chain the decanonization, wiki lookup,
       wiki article lookup, and freebase all together"""
    title = canonical.replace('_', ' ')
    try:
        wikiname, article = pick_wiki(canonical)
    except:
        print "Error in ", canonical
        wikiname, article = None, None
    notable, types = None, []
    for search in (wikiname, title):
        try:
            notable, types = get_freebase_types(search)
            break
        except:
            pass
    return dict(wikiname=wikiname, article=article, notable=notable,
                types=types)

img = r"http://upload.wikimedia.org/wikipedia/commons/thumb/5/51/"
img += r"Warren_Buffett_KU_Visit.jpg/220px-Warren_Buffett_KU_Visit.jpg"
text =  "Warren Edward Buffett (August 30, 1930) is an American "
text += "business magnate, investor, and philanthropist. He is widely considered "
text += "the most successful investor of... the 20th century."
fake_results = [dict(info=dict(wikiname='Warren Buffet', 
                article=dict(description=text),
                types=['type1a', 'typ1b']), 
                themes=['type 1', 'type 2'], 
                url="http://en.wikipedia.org/wiki/Warren_buffet", 
                title="Warren Buffet",
                description=text,
                notable="Wealthy Person",
                img=img,
                similarity=0.56)]
fake_other   = dict(query='query', translated='translated query',
                    wikinames=[])

class Expression(Actor):
    name = "Expression"
    max = 2
    skip_similar = True

    @timer
    def __init__(self, preloaded_actor=None, subsampling=False, 
                 fast=False, test=True):
        """We need to load and preprocess all of the vectors into the 
           memory and persist them to cut down on IO costs"""
        if not preloaded_actor:
            # a= 'all'
            # w='wikipedia'
            trained = "data" 
            #fnw = '%s/vectors.fullwiki.1000.s50.5k.words' % trained
            fnw = '%s/vectors.fullwiki.1000.s50.words' % trained
            fnw = '%s/freebase.words' % trained
            if False:
                wc2t = '%s/c2t' % './data'
                wt2c = '%s/t2c' % './data'
                # all word vecotor lib VL
                self.wc2t = cPickle.load(open(wc2t))
                self.wt2c = cPickle.load(open(wt2c))
                print "Loading...", 
                ks, vs  = [], []
                for k, v in self.wc2t.iteritems():
                    k = veclib.canonize(k, {}, match=False)
                    ks.append(k)
                    vs.append(v)
                for k, v in zip(ks, vs):
                    self.wc2t[k] = v
                print " done with veclib"
            # all words, word to index mappings w2i
            if os.path.exists(fnw + '.pickle'):
                self.aw2i , self.ai2w = cPickle.load(open(fnw + '.pickle'))
            else:
                self.aw2i , self.ai2w = veclib.get_words(fnw)
                cPickle.dump([self.aw2i, self.ai2w], open(fnw + '.pickle','w'))
            print " done with aw2i"
        else:
            # Wikipedia articles and their canonical transformations
            if False:
                self.wc2t = preloaded_actor.wc2t #Wiki dump article titles
                self.wt2c = preloaded_actor.wt2c
            # All vectors from word2vec
            self.aw2i = preloaded_actor.aw2i
            self.ai2w = preloaded_actor.ai2w

    def validate(self, query):
        return ',' not in query

    @timer
    def parse(self, query):
        """Debug with parallel=False, production use
        switch to multiprocessing"""
        # Split the query and find the signs of every word
        if query == 'None':
            return fake_results, fake_other
        words = query.replace('+', '|').replace('-', '|').replace(',', '|')
        words = words.replace(',','|')
        sign  = eval_sign(query)
        signs = ['+',]
        signs.extend([sign[match.start() + 1] \
                  for match in re.finditer('\|', words)])
        signs = [1.0 if s=='+' else -1.0 for s in signs]
        words = words.split('|')
        return signs, words

    @persist_to_file
    @timer
    def canonize(self, signs, words, parallel=False):
        # Get the canonical names for the query
        canon = self.aw2i.keys()
        if parallel:
            wc = lambda x: wiki_canonize(x, canon, use_wiki=False)
            rets  = [wiki_canonize(words[0], canon, use_wiki=True)]
            rets += parmap(wc, words[1:])
        else:
            rets  = [wiki_canonize(words[0], canon, use_wiki=True)]
            rets += [wiki_canonize(w, canon, use_wiki=False) for w in words[1:]]
        canonizeds, wikinames = zip(*rets)
        print rets
        if wikinames[0] is None:
            return '', [], [], []
        wikinames = [w if len(w)>0 else c for c, w in zip(canonizeds, wikinames)]
        # Make the translated query string
        translated = ""
        for sign, canonized in zip(signs, canonizeds):
            translated += "%+1.0f %s " %(sign, canonized)
        print 'translated: ', translated
        return translated, signs, canonizeds, wikinames

    @persist_to_file
    @timer
    def request(self, signs, canonizeds, parallel=True):
        # Format the vector lib request
        n = 8
        results = []
        iter = 0
        while len(results) < 2 and n < 21:
            args = []
            for sign, canonical in zip(signs, canonizeds):
                args.append([sign, canonical])
            send = json.dumps(dict(args=args))
            url = backend_url_nearest + urllib2.quote(send)
            response = json.load(urllib2.urlopen(url))
            # Decanonize the results and get freebase, article info
            if parallel:
                rv = parmap(result_chain, response['result'][:n])
            else:
                rv = [result_chain(x) for x in response['result'][:n]]
            args = (response['result'], response['similarity'], 
                    response['root_similarity'], rv)
            args = sorted(zip(*args), key=lambda x:x[1])[::-1]
            results = []
            for c, s, r, v in args:
                print '%1.3f %1.3f %s' % (s, r, v['wikiname'])
                if r > 0.90:
                    print 'Too similar to root'
                    continue
                if r > 0.75 and iter==0:
                    print 'Somewhat similar to root'
                    continue
                if v['wikiname'] is None:
                    print 'No wikiname'
                    continue
                if 'PA474' in v['wikiname']:
                    print 'skipping pa474'
                    continue
                ret = dict(canonical=c, similarity=s)
                ret.update(v)
                ret.update(ret.pop('article'))
                results.append(ret)
            n += 8
            iter += 1
        print "%i results" % len(results)
        return results, {}
    
    @timer
    def evaluate(self, query, translated, wikinames, results, other):
        temp = dict(query=query, translated=translated, 
                     wikinames=wikinames, query_text=query,
                     actor=self.name)
        other.update(temp)
        previous_titles = []
        rets = []
        for dresult in results:
            if len(rets) > self.max: break
            wikiname = dresult['wikiname']
            if self.skip_similar:
                if dresult['wikiname'] in other['wikinames']:
                    print 'Skipping direct in query', wikiname
                    continue
                if wikiname in previous_titles: 
                    print 'Skipping previous', wikiname
                    continue
            result = {}
            result['themes'] = dresult['types'][:3]
            if len(result['themes']) == 0:
                print 'Detected zero themes'
                del result['themes']
            result.update(dresult)
            if 'similarity' in result:
                result['similarity'] = "%1.2f" % result['similarity']
            if 'n1' in result:
                result['n1'] = "%1.2f" % result['n1']
            if 'title' not in result or result['title'] is None:
                result['title'] = resultresult['canonical']
            rets.append(result)
            previous_titles.append(wikiname)
        if len(rets) == 0:
            print 'no results kept'
            return {}
        else:
            reps = dict(results=rets)
            reps.update(other)
            return reps

    def run(self, query):
        start = time.time()
        signs, words = self.parse(query)
        translated, signs, canonizeds, wikinames = self.canonize(signs, words)
        if len(wikinames) > 0:
            results, other = self.request(signs, canonizeds)
            reps = self.evaluate(query, translated, wikinames, results, other)
            reps['actor'] = self.name
            reps['hostname'] = socket.gethostname()
            stop = time.time()
            reps['query_time'] = "%1.1f" %(stop - start)
            return reps
        else:
            reps = dict(translated="Wikipedia failed to respond; maybe wait a minute?")
            return reps

class Fraud(Expression):
    max = 2
    name = "Fraud"
    skip_similar = False
    def validate(self, query):
        return ',' in query

    @timer
    @persist_to_file
    def request(self, signs, canonizeds, parallel=True):
        # Format the vector lib request
        n = 6
        args = []
        for sign, canonical in zip(signs, canonizeds):
            args.append(canonical)
        send = json.dumps(dict(args=args))
        url = backend_url_farthest + urllib2.quote(send)
        response = json.load(urllib2.urlopen(url))
        args = response['args']
        self.max = len(args)
        # Decanonize the results and get freebase, article info
        if parallel:
            rv = parmap(result_chain, args[:n])
        else:
            rv = [result_chain(x) for x in args[:n]]
        results = []
        rw = response['right_word']
        r  = response['right']
        l  = response['left']
        print response['left_freebase']
        print response['inner']
        print response['right_freebase']
        for n1, w, v in zip(response['N1'], response['args'], rv):
            ret = {}
            m = 'x' if w == rw else 'o'
            print "%s %s %1.1f" % (m, w, n1)
            ret['mark'] = m
            ret['canonical'] = w
            ret['themes'] = r if m == 'x' else l
            ret['themes'] = ret['themes'][:4]
            ret['n1'] = n1
            ret.update(v)
            article = ret.pop('article')
            if article is not None:
                ret.update(article)
            results.append(ret)
        results = sorted(results, key=lambda x: x['n1'])
        left  = [prettify(lw) for lw in l if countdig(lw) < 2]
        right = [prettify(rw) for rw in r if countdig(rw) < 2]
        other = dict(left=left[:4], right=right[:4])
        return results, other

