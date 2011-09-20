import random
import itertools
import datetime
import Utils
import re
import csv
import bisect
import math
import copy
import operator
import sys
from os.path import commonprefix

maxInterpolateTime = 2.0*60.0*60.0	# 2 hours.

#------------------------------------------------------------------------------
# Define a global current race.
race = None

def getRace():
	global race
	return race

def newRace():
	global race
	race = Race()
	return race

def setRace( r ):
	global race
	race = r
	if race:
		race.resetCache()

def resetCache():
	global race
	if race:
		race.resetCache()
		
#----------------------------------------------------------------------
class Category(object):

	badRangeCharsRE = re.compile( '[^0-9,\-]' )

	def _getStr( self ):
		s = [str(i[0]) if i[0] == i[1] else '%d-%d' % i for i in self.intervals]
		s.extend( str(-e) for e in sorted(list(self.exclude)) )
		return ','.join( s )

	def _setStr( self, s ):
		s = Category.badRangeCharsRE.sub( '', str(s) )
		self.intervals = []
		self.exclude = set()
		fields = s.split(',')
		for f in fields:
			if not f:
				continue
			try:
				bounds = f.split( '-' )
				if not bounds:
					continue

				# Negative numbers are exceptions to remove from the ranges.
				if not bounds[0]:
					if len(bounds) > 1:
						self.exclude.add( int(bounds[1]) )
					continue

				bounds = [int(b) for b in bounds if b is not None and b != '']
				if not bounds:
					continue

				if len(bounds) > 2:			# Ignore numbers that are not in proper x-y format.
					del bounds[2:]
				elif len(bounds) == 1:
					bounds.append( bounds[0] )
				if bounds[0] > bounds[1]:
					bounds[0], bounds[1] = bounds[1], bounds[0]
				self.intervals.append( tuple(bounds) )
			except:
				# Ignore any parsing errors.
				pass
				
		self.intervals.sort()

	catStr = property(_getStr, _setStr)

	def getMask( self ):
		''' Return the common number prefix for all intervals (None if non-existent). '''
		mask = None
		for i in self.intervals:
			for k in i:
				num = str(k)
				if len(num) < 3:				# No mask for 1 or 2-digit numbers
					return None
				if mask is None:
					mask = num
				elif len(mask) != len(num):		# No mask for numbers of different lengths
					return None
				else:
					cp = commonprefix([mask, num])
					if not cp:
						return None
					mask = cp.ljust(len(mask), '.')
		return mask

	def __init__( self, active, name, catStr = '', startOffset = '00:00', numLaps = None, sequence = 0 ):
		self.active = False
		active = str(active).strip()
		if active and active[0] in 'TtYy1':
			self.active = True
		self.name = name
		self.catStr = catStr
		self.startOffset = startOffset
		try:
			self._numLaps = int(numLaps)
		except (ValueError, TypeError):
			self._numLaps = None
		try:
			self.sequence = int(sequence)
		except (ValueError, TypeError):
			self.sequence = 0
		
	def __setstate( self, d ):
		self.__dict__.update(d)
		i = getattr( self, 'intervals', None )
		if i:
			i.sort()
		
	def getNumLaps( self ):
		return getattr( self, '_numLaps', None )
		
	def setNumLaps( self, numLaps ):
		try:
			numLaps = int(numLaps)
		except ValueError:
			numLaps = None
		self._numLaps = numLaps if numLaps else None
		
	numLaps = property(getNumLaps, setNumLaps) 

	def matches( self, num ):
		if not self.active or num in self.exclude:
			return False
		i = bisect.bisect_left( self.intervals, (num, num) )
		if i > 0:
			i -= 1
		for j in xrange(i, min(i+2,len(self.intervals)) ):
			if self.intervals[j][0] <= num <= self.intervals[j][1]:
				return True
		return False

	def __cmp__( self, c ):
		for attr in ['sequence', 'name', 'active', 'startOffset', '_numLaps', 'catStr']:
			cCmp = cmp( getattr(self, attr, None), getattr(c, attr, None) )
			if cCmp != 0:
				return cCmp 
		return 0

	def __repr__( self ):
		return 'Category(active=%s, name="%s", catStr="%s", startOffset="%s", numLaps=%s, sequence=%s)' % (
				str(self.active),
				self.name,
				self.catStr,
				self.startOffset,
				str(self.numLaps),
				str(self.sequence) )

	def getStartOffsetSecs( self ):
		return Utils.StrToSeconds( self.startOffset )

#------------------------------------------------------------------------------------------------------------------

class Entry(object):
	# Store entries as tuples in sort sequence to improve performance.

	__slots__ = ('data')							# Suppress the default dictionary to save space.

	def __init__( self, num, lap, t, interp ):
		self.data = (t, -lap, num, interp)			# -lap sorts most laps covered to the front.

	def __cmp__( self, e ):
		return cmp( self.data, e.data )

	def set( self, e ):
		self.data = copy.copy(e.data)
		
	def __hash__( self ):
		return hash(e.data)

	@property
	def t(self):		return self.data[0]
	@property
	def lap(self):		return -self.data[1]
	@property
	def lapNeg(self):	return self.data[1]		# Negative number of laps (for sorting)
	@property
	def num(self):		return self.data[2]
	@property
	def interp(self):	return self.data[3]

	def __repr__( self ):
		return 'Entry( num=%d, lap=%d, interp=%s, t=%s )' % (self.num, self.lap, str(self.interp), str(self.t))

class Rider(object):
	# Rider Status.
	Finisher  = 0
	DNF       = 1
	Pulled    = 2
	DNS       = 3
	DQ 		  = 4
	OTL		  = 5
	NP		  = 6
	statusNames = ['Finisher', 'DNF', 'PUL', 'DNS', 'DQ', 'OTL', 'NP']

	# Factors for range of acceptable lap times.
	pMin, pMax = 0.85, 1.20
	
	# Maximum entries generated by interpolation.
	entriesMax = 22
		
	def __init__( self, num ):
		self.num = num
		self.times = []
		self.lapAdjust = 0
		self.status = Rider.Finisher
		self.tStatus = None

	def addTime( self, t ):
		# All times in race time seconds.
		timesLen = len(self.times)
		if timesLen == 0 or t > self.times[timesLen-1]:
			self.times.append( t )
		else:
			i = bisect.bisect_left(self.times, t, 0, timesLen)
			if i >= timesLen or self.times[i] != t:
				self.times.insert( i, t )

	def deleteTime( self, t ):
		try:
			self.times.remove( t )
		except ValueError:
			pass

	def getTimeCount( self ):
		if not self.times:
			return 0.0, 0					# No times, no count.
			
		# If there is only the first lap, return that.
		if len(self.times) == 1:
			return self.times[0], 1
			
		# If there is more than one lap, return the time from the other laps.
		return self.times[-1] - self.times[0], len(self.times) - 1

	def getLastKnownTime( self ):
		try:
			return self.times[-1]
		except IndexError:
			return 0

	def isDNF( self ):			return self.status == Rider.DNF
	def isDNS( self ):			return self.status == Rider.DNS
	def isPulled( self ):		return self.status == Rider.Pulled

	def setStatus( self, status, tStatus = None ):
		if status in [Rider.Finisher, Rider.DNS, Rider.DQ]:
			tStatus = None
		elif status in [Rider.Pulled, Rider.DNF]:
			if tStatus is None:
				race = getRace()
				if race:
					tStatus = race.lastRaceTime()
		
		self.status = status
		self.tStatus = tStatus			
	
	def getCleanLapTimes( self ):
		if not self.times or self.status in [Rider.DNS, Rider.DQ]:
			return None

		# Create a seperate working list.
		iTimes = [None] * (len(self.times) + 1)
		# Add a zero start time for the beginning of the race.
		# This avoids a whole lot of special cases later.
		iTimes[0] = 0.0
		iTimes[1:] = self.times

		averageLapTime = race.getAverageLapTime() if race else iTimes[-1] / float(len(iTimes) - 1)
		mustBeRepeatInterval = averageLapTime * 0.5

		# Remove duplicate entries.
		while len(iTimes) > 2:
			try:
				i = (i for i in xrange(len(iTimes) - 1, 0, -1) \
						if iTimes[i] - iTimes[i-1] < mustBeRepeatInterval).next()
				if i == 1:
					iDelete = i				# if the short interval is the first one, delete i
				elif i == len(iTimes) - 1:
					iDelete = i - 1			# if the short interval is the last one, delete i - 1
				else:
					#
					# Delete the entry that equalizes the time on each side.
					# -------g-------h---i---------j---------
					#
					g = i - 2
					h = i - 1
					j = i + 1
					gh = iTimes[h] - iTimes[g]
					ij = iTimes[j] - iTimes[i]
					iDelete = i - 1 if gh < ij else i
				del iTimes[iDelete]
			except StopIteration:
				break

		return iTimes

	def getExpectedLapTime( self, iTimes = None ):
		if iTimes is None:
			iTimes = self.getCleanLapTimes()
			if iTimes is None:
				return None

		if len(iTimes) == 2:
			# If only one lap is known, rely on the global average.
			#return getRace().getAverageLapTime()
			return iTimes[-1]

		# Ignore the first lap time as there is often a staggered start.
		if len(iTimes) > 2:
			iStart = 2
		else:
			iStart = 1

		# Compute differences between times.
		dTimes = [iTimes[i] - iTimes[i-1] for i in xrange(iStart, len(iTimes))]

		dTimes.sort()
		median = dTimes[int(len(dTimes) / 2)]

		mMin = median * Rider.pMin
		mMax = median * Rider.pMax

		#print 'median = %f' % median

		# Compute the average lap time (ignore times that are way off based on the median).
		# Check the most common case first (no wacky lap times).
		if mMin < dTimes[0] and dTimes[-1] < mMax:
			return sum(dTimes, 0.0) / len(dTimes)

		# Ignore the outliers and compute the average based on the core data.
		iLeft  = (i for i in xrange(0, len(dTimes))     if dTimes[i]   > mMin).next()
		iRight = (i for i in xrange(len(dTimes), 0, -1) if dTimes[i-1] < mMax).next()
		return sum(dTimes[iLeft:iRight], 0.0) / (iRight - iLeft)

	def interpolate( self, stopTime = maxInterpolateTime ):
		if not self.times or self.status in [Rider.DNS, Rider.DQ]:
			return []

		# Adjust the stop time.
		st = stopTime
		dnfPulledTime = None
		if self.status in [Rider.DNF, Rider.Pulled]:
			# If no given time, use the last recorded time for DNF and Pulled riders.
			dnfPulledTime = self.tStatus if self.tStatus is not None else self.times[-1]
			st = min(st, dnfPulledTime + 0.01)
		
		iTimes = self.getCleanLapTimes()
		expected = self.getExpectedLapTime( iTimes )

		# Flag that these are not interpolated times.
		iTimes = [(t, False) for t in iTimes]

		# Check for missing lap data and fill it in.
		for missing in xrange(1, 3):
			mMin = expected * missing + expected * Rider.pMin
			mMax = expected * missing + expected * Rider.pMax
			for j in (j for j in xrange(len(iTimes)-1, 0, -1) if mMin < (iTimes[j][0] - iTimes[j-1][0]) < mMax):
				tStart = iTimes[j-1][0]
				interp = float(iTimes[j][0] - tStart) / float(missing + 1)
				fill = [(tStart + interp * m, True) for m in xrange(1, missing+1)]
				iTimes[j:j] = fill

		# Pad out to one entry exceeding stop time if we are less than it.
		tBegin = iTimes[-1][0]
		if tBegin < st and len(iTimes) < Rider.entriesMax:
			tBegin += expected
			iMax = max( 1, int(math.ceil(st - tBegin) / expected) )
			iMax = min( iMax, Rider.entriesMax - len(iTimes) )
			iTimes.extend( [(tBegin + expected * i, True) for i in xrange(iMax)] )

		# Remove any entries exceeding the dnfPulledTime.
		if dnfPulledTime is not None and tBegin > dnfPulledTime:
			i = bisect.bisect_right( iTimes, (dnfPulledTime, False) )
			while i < len(iTimes) and iTimes[i][0] <= dnfPulledTime:
				i += 1
			del iTimes[i:]
		
		return [Entry(t=it[0], lap=i, num=self.num, interp=it[1]) for i, it in enumerate(iTimes)]
		
	def hasInterpolatedTime( self, tMax ):
		interpolate = self.interpolate()
		try:
			return any( e.interp for e in interpolate if e.t <= tMax )
		except (ValueError, StopIteration):
			return False

class Race(object):
	cacheAttr = ['entriesCache', 'leaderInfoCache', 'maxLapCache', 'maxAnyLapCache',
				 'averageLapTimeCache', 'rule80BeginTimeCache', 'rule80EndTimeCache',
				 'leaderTimesCache', 'leaderNumsCache', 'categoryTimesNumsCache', 'categoryCache']

	finisherStatusList = [Rider.Finisher, Rider.Pulled]
	finisherStatusSet = set( finisherStatusList )
	
	nonFinisherStatusList = [Rider.DNF, Rider.DNS, Rider.DQ, Rider.NP, Rider.OTL]
	nonFinisherStatusSet = set( nonFinisherStatusList )
	
	def __init__( self ):
		self.reset()

	def reset( self ):
		self.name = '<RaceName>'
		self.organizer = '<Organizer>'
		self.raceNum = 1
		self.date = datetime.date.today().strftime('%Y-%m-%d')
		self.scheduledStart = '10:00'
		self.minutes = 60
		self.commissaire = '<Commissaire>'
		self.memo = '<RaceMemo>'

		self.categories = {}
		self.riders = {}
		self.startTime = None
		self.finishTime = None
		self.numLaps = None

		self.isChangedFlag = True
		
		self.resetCache()
		
	def hasRiders( self ):
		return len(self.riders) > 0

	def isChanged( self ):
		return getattr(self, 'isChangedFlag', False)

	def setChanged( self, changed = True ):
		self.isChangedFlag = changed
		
	def resetCache( self ):
		for a in Race.cacheAttr:
			setattr( self, a, None )

	def popCache( self ):
		pop = [ getattr(self, a, None) for a in Race.cacheAttr ]
		self.resetCache()
		return pop

	def pushCache( self, c ):
		for a, v in zip( Race.cacheAttr, c ):
			setattr( self, a, v )

	def isRunning( self ):
		return self.startTime is not None and self.finishTime is None

	def isUnstarted( self ):
		return self.startTime is None

	def isFinished( self ):
		return self.startTime is not None and self.finishTime is not None

	def startRaceNow( self ):
		race.startTime = datetime.datetime.now()
		self.setChanged()

	def finishRaceNow( self ):
		race.finishTime = datetime.datetime.now()
		self.setChanged()

	def set( self, values = None ):
		self.reset()
		if values is not None:
			for k, d in values.iteritems():
				if k in self.__dict__:
					self.__dict__[k] = d

	def getRider( self, num ):
		try:
			num = int(num,10)
		except TypeError:
			num = int(num)

		try:
			rider = self.riders[num]
		except KeyError:
			rider = Rider( num )
			self.riders[num] = rider
		return rider

	def getRiderNumbers( self ):
		return self.riders.keys()

	def __contains__( self, num ):
		return num in self.riders

	def __getitem__( self, num ):
		return self.riders[num]

	def curRaceTime( self ):
		if self.startTime is None:
			return 0.0
		tCur = datetime.datetime.now() - self.startTime
		return tCur.seconds + tCur.microseconds / 1000000.0

	def lastRaceTime( self ):
		if self.finishTime is not None:
			t = self.finishTime - self.startTime
			return t.seconds + t.microseconds / 1000000.0
		return self.curRaceTime()

	def addTime( self, num, t = None ):
		if t is None:
			t = self.curRaceTime()
		self.getRider(num).addTime( t )
		self.resetCache()
		self.setChanged()
		return t

	def importTime( self, num, t ):
		self.getRider(num).addtime( t )

	def deleteTime( self, num, t ):
		if not num in self.riders:
			return
		rider = self.riders[num]
		rider.deleteTime( t )
		# If there are no times for this rider, remove the rider entirely.
		if len(rider.times) == 0:
			del self.riders[num]
		self.resetCache()
		self.setChanged()

	def getLastKnownTime( self ):
		try:
			return max( r.getLastKnownTime() for r in self.riders.itervalues() )
		except ValueError:
			return 0.0

	def getBestLapTime( self, lap ):
		try:
			return min( (r.getBestLapTime(lap), n) for n, r in self.riders.iteritems() )
		except ValueError:
			return 0.0

	def getAverageLapTime( self ):
		if getattr(self, 'averageLapTimeCache', None) is not None:
			return self.averageLapTimeCache
		tTotal, count = 0.0, 0
		for r in self.riders.itervalues():
			t, c = r.getTimeCount()
			tTotal += t
			count += c
		if count > 0:
			self.averageLapTimeCache = tTotal / count
		else:
			self.averageLapTimeCache = 8.0 * 60.0	# Default to 8 minutes.
		return self.averageLapTimeCache

	def interpolate( self ):
		# return self.interpolateLapCase()
		
		if getattr(self, 'entriesCache', None) is not None:
			return self.entriesCache
		# Reduce memory management in the list assignment.
		self.entriesCache = [None] * Rider.entriesMax * len(self.riders)
		iCur, iEnd = 0, 0
		for rider in self.riders.itervalues():
			interpolate = rider.interpolate()
			iEnd = iCur + len(interpolate)
			self.entriesCache[iCur:iEnd] = interpolate
			iCur = iEnd
		del self.entriesCache[iEnd:]
		self.entriesCache.sort()
		return self.entriesCache

	def interpolateLapCase( self ):
		if getattr(self, 'entriesCache', None) is not None:
			return self.entriesCache

		riderInterpolate = {}
		realTimesMax = 0
		for rider in self.riders.itervalues():
			interpolate = rider.interpolate()
			riderInterpolate[rider] = interpolate
			if realTimesMax < 5 and interpolate:
				realTimesMax = max( realTimesMax, (i for i, e in enumerate(interpolate) if e.interp).next() )

		if realTimesMax == 3:
			timeSum, timeCount = 0.0, 0
			oneLapRiders = set()
			for rider, interpolate in riderInterpolate.iteritems():
				if not interpolate:
					continue
				realTimes = (i for i, e in enumerate(interpolate) if e.interp).next()
				if realTimes == 3:
					timeSum += interpolate[2].t
					timeCount += 2
				elif realTimes == 2:
					oneLapRiders.add( rider )
					
			expected = timeSum / timeCount
			mMin = expected + expected * Rider.pMin
			mMax = expected + expected * Rider.pMax
			
			for rider in oneLapRiders:
				interpolate = riderInterpolate[rider]
				if mMin < interpolate[1].t < mMax:
					riderLapTime = interpolate[1].t / 2.0
					interpolate = [ Entry(rider.num, i, i * riderLapTime, not i in [0, 2]) for i in xrange(Rider.entriesMax) ]
					riderInterpolate[rider] = interpolate
			
		# Reduce memory management in the list assignment.
		self.entriesCache = [None] * Rider.entriesMax * len(self.riders)
		iCur, iEnd = 0, 0
		for rider, interpolate in riderInterpolate.iteritems():
			iEnd = iCur + len(interpolate)
			self.entriesCache[iCur:iEnd] = interpolate
			iCur = iEnd
		del self.entriesCache[iEnd:]
		self.entriesCache.sort()
		return self.entriesCache

	def interpolateCategoryNumLaps( self ):
		entries = self.interpolate()
		if not entries:
			return []
		
		# Find the number of laps for the category of each rider.
		riderNumLapsMax = {}
		for r in self.riders.iterkeys():
			try:
				catNumLaps = self.getCategory(r).getNumLaps()
				riderNumLapsMax[r] = catNumLaps if catNumLaps else 500
			except AttributeError:
				riderNumLapsMax[r] = 500
		
		# Filter results so that only the allowed number of laps is returned.
		return [e for e in entries if e.lap <= riderNumLapsMax[e.num]]
		
	def interpolateLap( self, lap, useCategoryNumLaps = False ):
		entries = self.interpolate() if not useCategoryNumLaps else self.interpolateCategoryNumLaps()
		# Find the first occurance of the given lap.
		if not entries:
			return []

		if lap > self.getMaxAnyLap():
			lap = self.getMaxAnyLap()

		# Find the first entry for the given lap.
		try:
			iFirst = (i for i, e in enumerate(entries) if e.lap == lap).next()
			# Remove all entries except the next time for each rider after the given lap.
			seen = {}
			return entries[:iFirst] + [ seen.setdefault(e.num, e) for e in entries[iFirst:] if e.num not in seen ]
		except StopIteration:
			pass
			
		return entries

	def getRule80LapTime( self ):
		entries = self.interpolateLap(2)
		if not entries:
			return None

		# Find the first entry for the given lap.
		iFirst = (i for i, e in enumerate(entries) if e.lap == 1).next()
		try:
			iSecond = (i for i, e in enumerate(entries) if e.lap == 2).next()
		except StopIteration:
			iSecond = None

		# Try to figure out if we should use the first lap or the second.
		# The first lap may not be the same length as the second.
		if iSecond is not None:
			tFirst = entries[iFirst].t
			tSecond = entries[iSecond].t - tFirst
			tDifference = abs(tFirst - tSecond)
			tAverage = (tFirst + tSecond) / 2.0
			if tDifference / tAverage > 0.1:	# If there is more than 10% difference, use the second lap.
				t = tSecond
			else:
				t = max(tFirst, tSecond)	# Else, use the maximum of the two (aren't we nice!).
		else:
			t = entries[iFirst].t
		return t

	def getRule80CountdownTime( self ):
		tFirstLap = self.getRule80LapTime()
		if tFirstLap is None:
			return None
		return tFirstLap * 0.8

	def getRule80RemainingCountdown( self ):
		self.getLeaderTimeLap()
		if self.rule80BeginTimeCache is None:
			return None
		raceTime = self.lastRaceTime()
		if self.rule80BeginTimeCache <= raceTime <= self.rule80EndTimeCache:
			tRemaining = self.rule80EndTimeCache - raceTime
			if tRemaining < 0.5:
				tRemaining = None
			return tRemaining
		return None

	def getMaxLap( self ):
		if getattr(self, 'maxLapCache', None) is None:
			entries = self.interpolate()
			try:
				self.maxLapCache = max( (e.lap + self[e.num].lapAdjust for e in entries if not e.interp) )
			except ValueError:
				self.maxLapCache = 0
		return self.maxLapCache

	def getRaceLaps( self ):
		raceLap = self.getMaxLap()
		if self.numLaps is not None and self.numLaps < raceLap:
			raceLap = self.numLaps
		return raceLap

	def getMaxAnyLap( self ):
		if getattr(self, 'maxAnyLapCache', None) is None:
			entries = self.interpolate()
			if not entries:
				self.maxAnyLapCache = 0
			else:
				self.maxAnyLapCache = max( e.lap for e in entries )
		return self.maxAnyLapCache

	def getLeaderTimesNums( self ):
		if getattr(self, 'leaderTimesCache', None):
			return self.leaderTimesCache, self.leaderNumsCache
		
		self.leaderTimesCache, self.leaderNumsCache = None, None
		
		entries = self.interpolate()
		if entries:
			leaderTimesCache = [ 0.0 ]
			leaderNumsCache = [ None ]
			for e in entries:
				if e.lap == len(leaderTimesCache):
					leaderTimesCache.append( e.t )
					leaderNumsCache.append( e.num )
				
			self.leaderTimesCache, self.leaderNumsCache = leaderTimesCache, leaderNumsCache
		
		return self.leaderTimesCache, self.leaderNumsCache
	
	def getLeaderOfLap( self, lap ):
		leaderTimes, leaderNums = self.getLeaderTimesNums()
		try:
			return leaderNums[lap]
		except (TypeError, IndexError):
			return None
	
	def getCurrentLap( self, t ):
		leaderTimes, leaderNums = self.getLeaderTimesNums()
		try:
			return bisect.bisect_left(leaderTimes, t)
		except (TypeError, IndexError):
			return 0
	
	def getLeaderAtTime( self, t ):
		leaderTimes, leaderNums = self.getLeaderTimesNums()
		try:
			return leaderNums[bisect.bisect_left(leaderTimes, t, hi=len(leaderTimes) - 1)]
		except (TypeError, IndexError):
			return None
	
	def getLeaderTimeLap( self ):
		# Also compute the rule80 cutoffs.
		if getattr(self, 'leaderInfoCache', None) is not None:
			return self.leaderInfoCache

		# num, t, lap
		self.leaderInfoCache = (None, None, None)
		self.rule80BeginTimeCache, self.rule80EndTimeCache = None, None

		entries = self.interpolate()
		if not entries:
			return self.leaderInfoCache

		raceTime = self.lastRaceTime()
		tLeaderLastLap = None
		
		leaderTimes, leaderNums = self.getLeaderTimesNums()
		i = bisect.bisect_right( leaderTimes, raceTime, hi=len(leaderTimes) - 1 )
		self.leaderInfoCache = (leaderNums[i], leaderTimes[i], i-1)
		
		tLeaderLastLap = leaderTimes[i-1]

		if tLeaderLastLap is not None and getattr(self, 'leaderInfoCache', None) is not None and self.leaderInfoCache[0] is not None:
			aveLapTime = self.leaderInfoCache[1] / float(self.leaderInfoCache[2])
			if raceTime < self.minutes*60 + aveLapTime/2.0:
				self.rule80BeginTimeCache = tLeaderLastLap
				self.rule80EndTimeCache = tLeaderLastLap + self.getRule80CountdownTime()
		return self.leaderInfoCache

	def getLeader( self ):
		return self.getLeaderTimeLap()[0]

	def getLeaderLapTime( self ):
		leader = self.getLeader()
		if leader is None:
			return None
		return self.riders[leader].getExpectedLapTime()

	def getLeaderTime( self ):
		return self.getLeaderTimeLap()[1]

	def getCategoryTimesNums( self ):
		if self.categoryTimesNumsCache:
			return self.categoryTimesNumsCache
			
		ctn = {}
		
		activeCategories = [c for c in self.categories.itervalues() if c.active]
		activeCategories.sort()
		
		for c in activeCategories:
			ctn[c] = [[0.0],[None]]

		for e in self.interpolate():
			try:
				cat = (c for c in activeCategories if c.matches(e.num)).next()
				times, nums = ctn[cat]
				if len(times) == e.lap:
					times.append( e.t )
					nums.append( e.num )
			except StopIteration:
				pass
		
		self.categoryTimesNumsCache = ctn
		return self.categoryTimesNumsCache

	def getCatPrevLeaders( self, t ):
		''' Return a dict accessed by number referring to category. '''
		catNextLeaders = {}
		for c, (times, nums) in self.getCategoryTimesNums().iteritems():
			i = bisect.bisect_left( times, t, hi=len(times)-1 )
			catNextLeaders[nums[i-1]] = c
		return catNextLeaders
		
	def getCatNextLeaders( self, t ):
		''' Return a dict accessed by number referring to category. '''
		catNextLeaders = {}
		for c, (times, nums) in self.getCategoryTimesNums().iteritems():
			i = bisect.bisect_left( times, t, hi=len(times)-1 )
			catNextLeaders[nums[i]] = c
		return catNextLeaders
		
	def getPrevLeader( self, t ):
		leaderTimes, leaderNums = self.getLeaderTimesNums()
		try:
			return leaderNums[bisect.bisect_left(leaderTimes, t) - 1]
		except (TypeError, IndexError):
			return None
		
	def getNextLeader( self, t ):
		leaderTimes, leaderNums = self.getLeaderTimesNums()
		try:
			return leaderNums[bisect.bisect_left(leaderTimes, t, hi=len(leaderTimes) - 1)]
		except (TypeError, IndexError):
			return None
		
	def hasDNFRiders( self ):
		return any(r.status == Rider.DNF for r in self.riders.itervalues())

	def numDNFRiders( self ):
		return sum( (1 for r in self.riders.itervalues() if r.status == Rider.DNF) )

	def hasPulledRiders( self ):
		return any(r.status == Rider.Pulled for r in self.riders.itervalues())

	def numPulledRiders( self ):
		return sum( (1 for r in self.riders.itervalues() if r.status == Rider.Pulled) )

	def getCategories( self ):
		activeCategories = [c for c in self.categories.itervalues() if c.active]
		activeCategories.sort()
		return activeCategories

	def setCategoryMask( self ):
		self.categoryMask = ''
		
		masks = []
		for c in self.categories.itervalues():
			if not c.active:
				continue
			maskCur = c.getMask()
			if maskCur is None:
				return
			masks.append( maskCur )
		
		if not masks:
			return

		maskLen = len(masks[0])
		if any( len(m) != maskLen for m in masks ):
			return

		cp = commonprefix( masks )
		mask = cp.ljust( maskLen, '.' )
		self.categoryMask = mask

	def getCategoryMask( self ):
		if getattr(self, 'categoryMask', None) is None:
			self.setCategoryMask()
		return self.categoryMask

	def getAllCategories( self ):
		allCategories = [c for c in self.categories.itervalues()]
		allCategories.sort()
		return allCategories

	def setActiveCategories( self, active = None ):
		allCategories = self.getAllCategories()
		for i, c in enumerate(allCategories):
			c.active = True if active is None or i in active else False
		self.setChanged()

	def setCategories( self, nameStrTuples ):
		newCategories = dict( (name, Category(active, name, numbers, startOffset, raceLaps, i)) \
			for i, (active, name, numbers, startOffset, raceLaps) in enumerate(nameStrTuples) if name )

		if self.categories != newCategories:
			self.categories = newCategories
			self.setChanged()
			self.resetCategoryCache()
			
		self.setCategoryMask()

	def exportCategories( self, fp ):
		for c in self.categories.itervalues():
			fp.write( '%s|%s\n' % (c.name.replace('|',''), c.catStr) )

	def importCategories( self, fp ):
		categories = []
		for r, line in enumerate(fp):
			if not line:
				continue
			fields = line.strip().split('|')
			categories.append( (True, fields[0], fields[1], '00:00', None) )
		self.setCategories( categories )

	def isRiderInCategory( self, num, catName = None ):
		if not catName or catName == 'All':
			return True
		category = self.categories.get( catName, None )
		return category.matches(num) if category is not None else False

	def hasCategory( self, catName = None ):
		# Check if there is at least one rider in this category.
		if not catName or catName == 'All':
			return True
		return any( self.isRiderInCategory(num, catName) for num in self.riders.iterkeys() )

	def getCategoryName( self, num ):
		c = self.getCategory( num )
		return c.name if c else ''

	def getCategory( self, num ):
		# Check the cache for this rider.
		# If not there, find it and add it to the cache.
		cc = getattr( self, 'categoryCache', None )
		try:
			return cc[num]
		except (NameError, TypeError, KeyError):
			if not cc:
				cc = {}
			for c in self.categories.itervalues():
				if c.active and c.matches(num):
					cc[num] = c
					return c
		cc[num] = None
		return None
	
	def resetCategoryCache( self ):
		setattr( self, 'categoryCache', None )
	
	def getNextExpectedLeaderTNL( self, t ):
		leaderTimes, leaderNums = self.getLeaderTimesNums()
		if leaderTimes:
			i = bisect.bisect_left( leaderTimes, t )
			if 0 < i < len(leaderTimes):
				return leaderTimes[i], leaderNums[i], leaderTimes[i] - leaderTimes[i-1]
		return None, None, None
	
	def isLeaderExpected( self ):
		if not self.isRunning():
			return False

		# Get the leaders and entries.
		leader = self.getLeader()
		entries = self.interpolate()
		if not entries or leader is None:
			return False

		# Check if the leader is expected in the next few riders.
		pos = bisect.bisect_right( entries, Entry(num=0, lap=0, t=race.curRaceTime(), interp=False) )
		for i in xrange(pos, min(pos+5, len(entries))):
			if entries[i].num == leader:
				return True
		return False
		
	def getRiderNums( self ):
		return self.riders.keys()

	def getLastFinisherTime( self ):
		if self.numLaps is not None:
			lap = self.numLaps
		else:
			lap = self.getMaxLap()
		entries = self.interpolateLap( lap, useCategoryNumLaps = True )
		return entries[-1].t if entries else 0.0
		
	#---------------------------------------------------------------------------------------

	def getResultsList( self, catName = 'All', lap = None ):
		if not self.riders:
			return []
			
		if lap is None or lap > self.getMaxLap():
			lap = self.getMaxLap()
		if self.numLaps is not None and self.numLaps < lap:
			lap = self.numLaps
			
		category = self.categories.get( catName, None )
		if category and category.numLaps:
			lap = min( lap, category.numLaps )

		entries = self.interpolateLap( lap, True )
		if not entries:
			return []

		# Add the latest known time for every finished or pulled rider.
		finishers = []
		finishNums = set()
		finisherStatusSet = Race.finisherStatusSet
		for e in (e for e in reversed(entries) if e.num not in finishNums):
			finishNums.add( e.num )
			if 	race[e.num].status in finisherStatusSet and \
					(category is None or category.matches(e.num)):
				finishers.append( e )

		# Sort by laps completed, time and num.
		finishers.sort( key = lambda x: (-x.lap, x.t, x.num) )
		return finishers

	#----------------------------------------------------------------------------------------
	
	def getResults( self, catName = 'All' ):
		''' Output: colNames, finishers (includes pulled), dnf, dns, dq '''
		finishers = self.getResultsList( catName )
		if not finishers:
			colnames = []
			results = []
		else:
			# Format the timed results by laps down.
			maxLaps = finishers[0].lap
			results = []
			for e in finishers:
				lapsDown = maxLaps - (e.lap + self[e.num].lapAdjust)
				if lapsDown < 0:
					lapsDown = 0
				# print 'lapsDown=%d e.lap=%d lapAdjust=%d' % (lapsDown, e.lap, self[e.num].lapAdjust)
				while lapsDown >= len(results):
					results.append( [] )
				results[lapsDown].append( e )

			# Get the column labels and trim out the empty columns.
			colnames = [ str(-k) if k > 0 else str(maxLaps) for k, r in enumerate(results) if len(r) > 0 ]
			results = [ r for r in results if len(r) > 0 ]

		# Get the DNF, DNS and DQ riders.
		category = self.categories.get( catName, None )
		nonFinishersStatusSet = Race.nonFinisherStatusSet
		ridersSubset = [r for r in self.riders.itervalues()
							if r.status in nonFinishersStatusSet and 
								(category is None or category.matches(r.num))]
		nonFinishers = []
		for status in Race.nonFinisherStatusList:
			numTimes = [(r.num, r.tStatus if r.tStatus is not None else -sys.float_info.max) for r in ridersSubset if r.status == status]
			numTimes.sort( key = lambda x : (-x[1], x[0]) )
			nonFinishers.append( numTimes if numTimes else None )

		return colnames, results, nonFinishers[0], nonFinishers[1], nonFinishers[2]

	def allRidersFinished( self ):
		# This is dangerous!  Do not end the program early!  Always let the user end the race in case of additional laps.
		# Simply check that it has been 60 minutes since the race ended.
		if not self.isRunning():
			return True
			
		try:
			entries = self.interpolate()
			eLastRecorded = (e for e in reversed(entries) if not e.interp).next()
			return self.lastRaceTime() - eLastRecorded.t > 60.0*60.0
		except StopIteration:
			pass
			
		return False
		'''
		if race.numLaps is None:
			return False
		finishers = self.getResultsList( lap=race.numLaps )
		if finishers is None:
			return False
		return any(e.lap == race.numLaps for e in finishers) and not any(e.interp for e in finishers)
		'''

	def _populate( self ):
		self.reset()

		random.seed( 1010101 )
		mean = 5 * 60
		var = 30
		lapsTotal = 5
		riders = 30
		self.startTime = datetime.datetime.now() - datetime.timedelta(seconds=lapsTotal*mean + 4*60)
		for num in xrange(100,100+riders+1):
			t = 0
			mu = random.normalvariate( mean, var )	# Rider's random average lap time.
			for laps in xrange(lapsTotal):
				t += random.normalvariate(mu, var )	# Rider's lap time.
				self.addTime( num, t )
		if Utils.isMainWin():
			Utils.getMainWin().startRaceClock()

		for j, i in enumerate(xrange(100,100+riders+1,10)):
			name = 'Cat%d' % (j+1)
			self.categories[name] = Category(name, str(i) + '-' + str(i+9))

		self.setChanged()

if __name__ == '__main__':
	r = newRace()
	r.addTime( 10, 1 * 60 )
	r.addTime( 10, 5 * 60 )
	r.addTime( 10, 9 * 60 )
	r.addTime( 10, 10 * 60 )
	rider = r.getRider( 10 )
	entries = rider.interpolate( 11 )
	print [(Utils.SecondsToMMSS(e.t), e.interp) for e in entries]
	#sys.exit( 0 )
	
	r.addTime( 10,  5 )
	#r.addTime( 10, 10 )
	r.addTime( 10, 15 )
	r.addTime( 10, 15.05 )
	r.addTime( 10, 15.06 )
	r.addTime( 10, 20 )
	r.addTime( 10, 25 )
	r.addTime( 10, 30 )
	#r.addTime( 10, 35 )
	rider = r.getRider( 10 )
	entries = rider.interpolate( 36 )
	print [(e.t, e.interp) for e in entries]
	'''
	rider.lapAdjust = 4
	entries = rider.interpolate( 36 )
	print [e.t for e in entries]
	'''

	c = Category(True, 'test', '100-150-199,205,-50', '00:00', None)
	print c
	print 'mask=', c.getMask()
	c = Category(True, 'test', '100-199,-150', None)
	print 'mask=', c.getMask()
	c = Category(True, 'test', '1400-1499,-1450', None)
	print 'mask=', c.getMask()
	
	r.setCategories( [	(True, 'test1', '1100-1199', '00:00', None),
						(True, 'test2', '1200-1299, 2000,2001,2002', '00:00', None),
						(True, 'test3', '1300-1399', '00:00', None)] )
	print r.getCategoryMask()
	print r.getCategory( 2002 )

