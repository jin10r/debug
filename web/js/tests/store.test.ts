/**
 * @jest-environment jsdom
 */

import { store, getEventId, getEventTime } from '../core/store';

const makeFeature = (id: string | number, time: string, layer = 'pig'): any => ({
  type: 'Feature',
  properties: { id, message_id: id, time, layer },
  geometry: { type: 'Point', coordinates: [0, 0] },
});

describe('SurvivalStore', () => {
  beforeEach(() => {
    (window as any).serverNow = () => Date.now();
    store.setState({
      eventsById: new Map(),
      currentTimeFilter: 30,
      activeLayers: new Set(['pig', 'cops', 'bus', 'traffic']),
      revision: 0,
      clockTick: 0,
    });
  });

  describe('addEvent', () => {
    test('adds new event and returns true', () => {
      const feature = makeFeature(1, new Date(Date.now() - 1000).toISOString());
      expect(store.getState().addEvent(feature)).toBe(true);
      expect(store.getState().eventsById.size).toBe(1);
    });

    test('deduplicates existing event and returns false', () => {
      const feature = makeFeature(1, new Date(Date.now() - 1000).toISOString());
      store.getState().addEvent(feature);
      expect(store.getState().addEvent(feature)).toBe(false);
      expect(store.getState().eventsById.size).toBe(1);
    });
  });

  describe('addEvents', () => {
    test('adds multiple events and returns new count', () => {
      const features = [
        makeFeature(1, new Date(Date.now() - 1000).toISOString()),
        makeFeature(2, new Date(Date.now() - 2000).toISOString()),
      ];
      expect(store.getState().addEvents(features)).toBe(2);
      expect(store.getState().eventsById.size).toBe(2);
    });

    test('returns 0 for empty array', () => {
      expect(store.getState().addEvents([])).toBe(0);
    });
  });

  describe('updateTimeFilter', () => {
    test('changes time filter and bumps revision', () => {
      const rev = store.getState().revision;
      store.getState().updateTimeFilter(60);
      expect(store.getState().currentTimeFilter).toBe(60);
      expect(store.getState().revision).toBeGreaterThan(rev);
    });

    test('does nothing if same filter', () => {
      store.getState().updateTimeFilter(30);
      const rev = store.getState().revision;
      store.getState().updateTimeFilter(30);
      expect(store.getState().revision).toBe(rev);
    });
  });

  describe('toggleLayer', () => {
    test('toggles layer on and off', () => {
      const layers = store.getState().activeLayers;
      expect(layers.has('pig')).toBe(true);
      store.getState().toggleLayer('pig');
      expect(store.getState().activeLayers.has('pig')).toBe(false);
      store.getState().toggleLayer('pig');
      expect(store.getState().activeLayers.has('pig')).toBe(true);
    });
  });

  describe('pruneExpired', () => {
    test('removes events older than TTL (60min)', () => {
      const oldTime = new Date(Date.now() - 61 * 60 * 1000).toISOString();
      const newTime = new Date(Date.now() - 10 * 1000).toISOString();
      store.getState().addEvents([
        makeFeature(1, oldTime),
        makeFeature(2, newTime),
      ]);
      expect(store.getState().eventsById.size).toBe(1);
      const removed = store.getState().pruneExpired();
      expect(removed).toBe(0);
      expect(store.getState().eventsById.size).toBe(1);
    });

    test('hard cap at 5000 removes oldest overflow', () => {
      const features = Array.from({ length: 5100 }, (_, i) =>
        makeFeature(i, new Date(Date.now() - i * 100).toISOString())
      );
      store.getState().addEvents(features);
      expect(store.getState().eventsById.size).toBe(5100);
      const removed = store.getState().pruneExpired();
      expect(store.getState().eventsById.size).toBeLessThanOrEqual(5000);
      expect(removed).toBeGreaterThan(0);
    });
  });

  describe('clearEvents', () => {
    test('removes all events', () => {
      store.getState().addEvent(makeFeature(1, new Date().toISOString()));
      expect(store.getState().eventsById.size).toBe(1);
      store.getState().clearEvents();
      expect(store.getState().eventsById.size).toBe(0);
    });
  });
});

describe('getEventId', () => {
  test('extracts message_id as primary key', () => {
    expect(getEventId({ properties: { message_id: 42 } } as any)).toBe(42);
  });

  test('falls back to id if message_id missing', () => {
    expect(getEventId({ properties: { id: 'abc' } } as any)).toBe('abc');
  });

  test('returns null for feature without properties', () => {
    expect(getEventId({} as any)).toBeNull();
  });
});

describe('getEventTime', () => {
  test('parses ISO string with Z', () => {
    const d = getEventTime({ properties: { time: '2024-01-01T12:00:00Z' } } as any);
    expect(d).not.toBeNull();
    expect(d!.getUTCFullYear()).toBe(2024);
  });

  test('parses ISO string without offset (naive → UTC)', () => {
    const d = getEventTime({ properties: { time: '2024-01-01 12:00:00' } } as any);
    expect(d).not.toBeNull();
  });

  test('returns null for invalid time', () => {
    expect(getEventTime({ properties: { time: 'not-a-time' } } as any)).toBeNull();
  });
});
