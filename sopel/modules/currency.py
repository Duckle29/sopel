# coding=utf-8
# Copyright 2013 Elsie Powell, embolalia.com
# Copyright 2019 Mikkel Jeppesen
# Licensed under the Eiffel Forum License 2
from __future__ import unicode_literals, absolute_import, print_function, division

import re
import requests
import time

from sopel.config.types import StaticSection, ValidatedAttribute
from sopel.logger import get_logger
from sopel.module import commands, example, NOLIMIT, rule

FIAT_URL = 'https://api.exchangeratesapi.io/latest?base=EUR'
FIXER_URL = 'http://data.fixer.io/api/latest?base=EUR&access_key={}'
CRYPTO_URL = 'https://apiv2.bitcoinaverage.com/indices/global/ticker/short?crypto=BTC'
EXCHANGE_REGEX = re.compile(r'''
    ^(\d+(?:\.\d+)?)                                            # Decimal number
    \s*([a-zA-Z]{3})                                            # 3-letter currency code
    \s+(?:in|as|of|to)\s+                                       # preposition
    (([a-zA-Z]{3}$)|([a-zA-Z]{3})\s)+$                          # one or more 3-letter currency code
''', re.VERBOSE)

LOGGER = get_logger(__name__)
rates_fiat_json = {}
rates_btc_json = {}

# Config
class CurrencySection(StaticSection):
    fixer_io_key = ValidatedAttribute('fixer_io_key', default=None)
    auto_convert = ValidatedAttribute('auto_convert', parse=bool, default=False)


def configure(config):
    config.define_section('currency', CurrencySection, validate=False)
    config.currency.configure_setting('fixer_io_key', 'API key for fixer IO. Leave blank to use exchangeratesapi.io:')
    config.currency.configure_setting('auto_convert', 'Automatically respond to regex matches?')


def setup(bot):
    bot.config.define_section('currency', CurrencySection)
# End config


def update_rates(bot):
    global rates_fiat_json
    global rates_btc_json

    # If we have data that are less than 24h old, return
    if 'date' in rates_fiat_json:
        if time.time() - rates_fiat_json['date'] < 24 * 60 * 60:
            return

    # Update crypto rates
    request = requests.get(CRYPTO_URL)
    request.raise_for_status()
    rates_btc_json = request.json()

    # Update fiat rates
    if bot.config.currency.fixer_io_key is not None:
        request = requests.get(FIXER_URL.format(bot.config.currency.fixer_io_key))
        if not request.json()['success']:
            LOGGER.error(str(request.json()['error']))
            return bot.reply('Sorry, something went wrong')
    else:
        request = requests.get(FIAT_URL)

    request.raise_for_status()
    rates_fiat_json = request.json()
    rates_fiat_json['date'] = time.time()
    rates_fiat_json['rates']['EUR'] = 1.0  # Put this here to make logic easier


def btc_rate(code, reverse=False):

    search = 'BTC{}'.format(code)

    if search in rates_btc_json:
        rate = rates_btc_json[search]['averages']['day']
    else:
        return "Sorry {} isn't currently supported".format(code)

    if reverse:
        return 1 / rate
    else:
        return rate


def get_rate(of, to):
    of = of.upper()
    to = to.upper()

    if of == 'BTC' or to == 'BTC':
        if of == 'BTC':
            code = to
            reverse = False
        else:
            code = of
            reverse = True

        return btc_rate(code, reverse)

    if of not in rates_fiat_json['rates']:
        return "Sorry {} isn't currently supported".format(of)

    if to not in rates_fiat_json['rates']:
        return "Sorry {} isn't currently supported".format(to)

    return (1 / rates_fiat_json['rates'][of]) * rates_fiat_json['rates'][to]


def exchange(bot, match):
    """Show the exchange rate between two currencies"""

    if not match:
        bot.reply("Sorry, I didn't understand the input.")
        return NOLIMIT

    try:
        update_rates(bot)  # Try and update rates. Rate-limiting is done in update_rates()
    except requests.exceptions.RequestException as e:
        bot.reply("Something went wrong while I was getting the exchange rate.")
        LOGGER.error("Error in GET request: {}".format(e))
        return NOLIMIT
    except ValueError:
        bot.reply("Error: Got malformed data.")
        return NOLIMIT

    query = match.string

    others = query.split()
    amount = others.pop(0)
    of = others.pop(0)
    others.pop(0)

#    amount, of, _, *others = query.split() # I'd much rather use this, but it's not python 2.7 compatible

    try:
        amount = float(amount)
    except ValueError:
        bot.reply("Sorry, I didn't understand the input.")
    except OverflowError:
        bot.reply("Sorry, input amount was out of range.")

    out_string = '{} {} is'.format(amount, of.upper())
    for to in others:
        try:
            out_string = build_reply(bot, amount, of.upper(), to.upper(), out_string)
        except (requests.exceptions.HTTPError, requests.exceptions.ConnectionError) as e:
            bot.reply("Something went wrong while I was getting the exchange rate.")
            LOGGER.error("Error in GET request: {}".format(e))

            return NOLIMIT
        except ValueError:
            return NOLIMIT

    bot.reply(out_string[0:-1])


@commands('cur', 'currency', 'exchange')
@example('.cur 100 usd in btc cad eur')
def exchange_cmd(bot, trigger):
    if not trigger.group(2):
        return bot.reply("No search term. An example: .cur 100 usd in btc cad eur")

    match = EXCHANGE_REGEX.match(trigger.group(2))
    exchange(bot, match)


@rule(EXCHANGE_REGEX)
@example('100 usd in btc cad eur')
def exchange_re(bot, trigger):
    if bot.config.currency.auto_convert:
        match = EXCHANGE_REGEX.match(trigger)
        exchange(bot, match)


def build_reply(bot, amount, of, to, out_string):
    if not amount:
        bot.reply("Zero is zero, no matter what country you're in.")

    rate_raw = ''
    try:
        rate_raw = get_rate(of, to)
        rate = float(rate_raw)
    except ValueError:
        bot.reply(rate_raw)
        raise

    result = float(rate * amount)

    if to == 'BTC':
        return out_string + ' {:.5f} {},'.format(result, to)

    return out_string + ' {:.2f} {},'.format(result, to)
