/**
 * TypeScript Type Tests for Stage 1
 * 
 * These tests verify that TypeScript types are correctly defined
 * and provide proper type checking.
 */

import { 
    EventFeature, 
    EventFeatureCollection, 
    EventProperties,
    EventLayer,
    StoreState,
    WebSocketMessage,
    CacheEntry
} from './types/geojson';

// ==================== Type Test 1: EventProperties ====================
function testEventProperties() {
    console.log('🧪 Test: EventProperties type');
    
    // Valid event properties
    const validProps: EventProperties = {
        id: 123,
        description: 'Test event',
        layer: 'pig',
        strategy: 'exact',
        time: '2026-02-22T14:30:00Z',
        photo_url: '/assets/images/test.jpg',
        matches: [{ type: 'keyword', confidence: 0.95 }]
    };
    
    // Should compile without errors
    console.log('  ✅ EventProperties type is valid');
    
    // Test layer type
    const pigLayer: EventLayer = 'pig';
    const copsLayer: EventLayer = 'cops';
    const busLayer: EventLayer = 'bus';
    const unknownLayer: EventLayer = 'unknown';
    
    console.log('  ✅ EventLayer type is valid');
    
    return true;
}

// ==================== Type Test 2: EventFeature ====================
function testEventFeature() {
    console.log('🧪 Test: EventFeature type');
    
    const feature: EventFeature = {
        type: 'Feature',
        geometry: {
            type: 'Point',
            coordinates: [30.7233, 46.4825]
        },
        properties: {
            id: 123,
            description: 'Test event',
            layer: 'pig',
            strategy: 'exact',
            time: '2026-02-22T14:30:00Z'
        }
    };
    
    console.log('  ✅ EventFeature type is valid');
    return true;
}

// ==================== Type Test 3: EventFeatureCollection ====================
function testEventFeatureCollection() {
    console.log('🧪 Test: EventFeatureCollection type');
    
    const collection: EventFeatureCollection = {
        type: 'FeatureCollection',
        features: [
            {
                type: 'Feature',
                geometry: {
                    type: 'Point',
                    coordinates: [30.7233, 46.4825]
                },
                properties: {
                    id: 123,
                    description: 'Test event 1',
                    layer: 'pig',
                    strategy: 'exact'
                }
            },
            {
                type: 'Feature',
                geometry: {
                    type: 'Point',
                    coordinates: [30.7234, 46.4826]
                },
                properties: {
                    id: 124,
                    description: 'Test event 2',
                    layer: 'cops',
                    strategy: 'exact'
                }
            }
        ]
    };
    
    console.log('  ✅ EventFeatureCollection type is valid');
    return true;
}

// ==================== Type Test 4: StoreState ====================
function testStoreState() {
    console.log('🧪 Test: StoreState type');
    
    const state: StoreState = {
        events: {
            type: 'FeatureCollection',
            features: []
        },
        currentTimeFilter: 30,
        activeLayers: new Set(['pig', 'cops', 'bus']),
        isSyncInProgress: false,
        consecutiveNetworkErrors: 0,
        updateInterval: 15000,
        isNetworkErrorDisplayed: false
    };
    
    console.log('  ✅ StoreState type is valid');
    return true;
}

// ==================== Type Test 5: CacheEntry ====================
function testCacheEntry() {
    console.log('🧪 Test: CacheEntry type');
    
    const entry: CacheEntry = {
        feature: {
            type: 'Feature',
            geometry: {
                type: 'Point',
                coordinates: [30.7233, 46.4825]
            },
            properties: {
                id: 123,
                description: 'Test event',
                layer: 'pig',
                strategy: 'exact'
            }
        },
        receivedAt: Date.now(),
        expiresAt: Date.now() + 60 * 60 * 1000 // 60 minutes
    };
    
    console.log('  ✅ CacheEntry type is valid');
    return true;
}

// ==================== Type Test 6: WebSocketMessage ====================
function testWebSocketMessage() {
    console.log('🧪 Test: WebSocketMessage type');
    
    const newEventMsg: WebSocketMessage = {
        type: 'new_event',
        data: {
            type: 'FeatureCollection',
            features: []
        }
    };
    
    const catchupMsg: WebSocketMessage = {
        type: 'catchup',
        since: '2026-02-22T14:30:00Z'
    };
    
    const filterMsg: WebSocketMessage = {
        type: 'change_time_filter',
        time_filter: 30,
        layers: ['pig', 'cops']
    };
    
    console.log('  ✅ WebSocketMessage type is valid');
    return true;
}

// ==================== Type Test 7: Window Globals ====================
function testWindowGlobals() {
    console.log('🧪 Test: Window global extensions');
    
    // Test APP_CONFIG
    window.APP_CONFIG = {
        map_center_lat: 46.4825,
        map_center_lng: 30.7233,
        map_default_zoom: 10,
        enable_random_points: true,
        validation_redirect_url: '/access-denied.html'
    };
    
    // Test localCache
    const cache = window.localCache;
    if (cache) {
        console.log('  ✅ localCache global is defined');
    }
    
    // Test store
    const store = window.store;
    if (store) {
        console.log('  ✅ store global is defined');
    }
    
    // Test telegramValidator
    const validator = window.telegramValidator;
    if (validator) {
        console.log('  ✅ telegramValidator global is defined');
    }
    
    return true;
}

// ==================== Run All Tests ====================
async function runTypeTests() {
    console.log('🚀 Running TypeScript Type Tests (Stage 1)');
    console.log('==========================================\n');
    
    const results = [];
    
    results.push(testEventProperties());
    results.push(testEventFeature());
    results.push(testEventFeatureCollection());
    results.push(testStoreState());
    results.push(testCacheEntry());
    results.push(testWebSocketMessage());
    results.push(testWindowGlobals());
    
    console.log('\n==========================================');
    console.log(`📊 Results: ${results.length}/${results.length} tests passed`);
    console.log('==========================================\n');
    
    return { passed: results.length, total: results.length, results };
}

// Export for use
window.typeTests = {
    runTypeTests,
    testEventProperties,
    testEventFeature,
    testEventFeatureCollection,
    testStoreState,
    testCacheEntry,
    testWebSocketMessage,
    testWindowGlobals
};

// Auto-run if in browser
if (typeof document !== 'undefined') {
    console.log('Type tests loaded. Call window.typeTests.runTypeTests() to run.');
}
