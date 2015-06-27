import logging
import random
from itertools import chain
from .actions import Draw, Play, Give, Summon
from .card import BaseCard
from .deck import Deck
from .entity import Entity
from .enums import CardType, PlayState, PowSubType, Zone
from .entity import slot_property
from .managers import PlayerManager
from .targeting import *
from .utils import CardList


class Player(Entity):
	Manager = PlayerManager
	extra_deathrattles = slot_property("extra_deathrattles")
	outgoing_healing_adjustment = slot_property("outgoing_healing_adjustment")
	type = CardType.PLAYER

	def __init__(self, name):
		self.data = None
		super().__init__()
		self.name = name
		self.deck = Deck()
		self.hand = CardList()
		self.field = CardList()
		self.secrets = CardList()
		self.buffs = []
		self.max_hand_size = 10
		self.max_resources = 10
		self.current_player = False
		self.fatigue_counter = 0
		self.hero = None
		self.last_card_played = None
		self.overloaded = 0
		self.max_mana = 0
		self.playstate = PlayState.INVALID
		self.temp_mana = 0
		self.timeout = 75
		self.times_hero_power_used_this_game = 0
		self.minions_killed_this_turn = 0
		self.weapon = None
		self.zone = Zone.INVALID

	def __str__(self):
		return self.name

	def __repr__(self):
		return "%s(name=%r, hero=%r)" % (self.__class__.__name__, self.name, self.hero)

	@property
	def controller(self):
		return self

	@property
	def slots(self):
		return self.buffs

	@property
	def mana(self):
		mana = max(0, self.max_mana - self.used_mana) + self.temp_mana
		return mana

	@property
	def spellpower(self):
		return sum(minion.spellpower for minion in self.field)

	@property
	def characters(self):
		return CardList(chain([self.hero] if self.hero else [], self.field))

	@property
	def entities(self):
		ret = []
		for entity in self.field:
			ret += entity.entities
		# Secrets are only active on the opponent's turn
		if not self.current_player:
			for entity in self.secrets:
				ret += entity.entities
		return CardList(chain(list(self.hero.entities) if self.hero else [], ret, [self]))

	@property
	def liveEntities(self):
		ret = self.field[:]
		if self.hero:
			ret.append(self.hero)
		if self.weapon:
			ret.append(self.weapon)
		return ret

	@property
	def opponent(self):
		# Hacky.
		return [p for p in self.game.players if p != self][0]

	def give(self, id):
		cards = self.game.queue_actions(self, [Give(self, id)])[0]
		return cards[0]

	def getById(self, id):
		"Helper to get a card from the hand by its id"
		for card in self.hand:
			if card.id == id:
				return card
		raise ValueError

	def prepare_deck(self, cards, hero):
		self.originalDeck = Deck.fromList(cards)
		self.originalDeck.hero = hero

	def discard_hand(self):
		logging.info("%r discards his entire hand!" % (self))
		# iterate the list in reverse so we don't skip over cards in the process
		# yes it's stupid.
		for card in self.hand[::-1]:
			card.discard()

	def draw(self, count=1):
		ret = self.game.queue_actions(self, [Draw(self) * count])[0]
		if count == 1:
			return ret[0]
		return ret

	def mill(self, count=1):
		if count == 1:
			if not self.deck:
				return
			else:
				card = self.deck[-1]
			logging.info("%s mills %r" % (self, card))
			card.destroy()
			return card
		else:
			ret = []
			while count:
				ret.append(self.mill())
				count -= 1
			return ret

	def fatigue(self):
		self.fatigue_counter += 1
		logging.info("%s takes %i fatigue damage" % (self, self.fatigue_counter))
		self.hero.hit(self.hero, self.fatigue_counter)

	@property
	def max_mana(self):
		return self._max_mana

	@max_mana.setter
	def max_mana(self, amount):
		self._max_mana = min(self.max_resources, max(0, amount))
		logging.info("%s is now at %i mana crystals", self, self._max_mana)

	def takeControl(self, card):
		logging.info("%s takes control of %r", self, card)
		zone = card.zone
		card.zone = Zone.SETASIDE
		card.controller = self
		card.zone = zone

	def shuffle_deck(self):
		logging.info("%r shuffles their deck", self)
		random.shuffle(self.deck)

	def summon(self, card):
		"""
		Puts \a card in the PLAY zone
		"""
		if isinstance(card, str):
			card = self.game.card(card)
			card.controller = self
		self.game.queue_actions(self, [Summon(self, card)])
		return card

	def play(self, card, target=None, choose=None):
		return self.game.queue_actions(self, [Play(card, target, choose)])

	def _play(self, card):
		"""
		Plays \a card from the player's hand
		"""
		logging.info("%s plays %r from their hand" % (self, card))
		assert card.controller
		cost = card.cost
		if self.temp_mana:
			# The coin, Innervate etc
			cost -= self.temp_mana
			self.temp_mana = max(0, self.temp_mana - card.cost)
		self.used_mana += cost
		if card.overload:
			logging.info("%s overloads for %i mana", self, card.overload)
			self.overloaded += card.overload
		self.last_card_played = card
		self.summon(card)
		self.combo = True
		self.cards_played_this_turn += 1
		if card.type == CardType.MINION:
			self.minions_played_this_turn += 1
