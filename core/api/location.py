"""Location search API handlers.

Provides endpoints for finding locations by text query.
Uses PostgreSQL SQL functions for pg_trgm search and PostGIS geometry calculations.
"""

import logging
from typing import Dict, Any, Optional
from aiohttp import web

from core.settings import settings

logger = logging.getLogger(__name__)


async def get_location_search_handler(request: web.Request) -> web.Response:
    """
    Search for location by text query.
    
    Query Parameters:
        q (str): Search query text (required)
        
    Returns:
        JSON response with:
        - name: Location name or 'Random Point'
        - coordinates: [lon, lat] for map centering
        - geom: Full GeoJSON geometry
        - matches: List of matched streets with scores
        - strategy: Matching strategy used
    """
    try:
        # Get query parameter
        query = request.query.get('q', '').strip()
        
        if not query:
            return web.json_response(
                {'error': 'Query parameter "q" is required'},
                status=400
            )
        
        if len(query) < 3:
            return web.json_response(
                {'error': 'Query must be at least 3 characters long'},
                status=400
            )
        
        # Get location service from app context
        location_service = request.app.get('location_service')
        
        if not location_service:
            logger.error("LocationService not available in app context")
            return web.json_response(
                {'error': 'Location service not available'},
                status=503
            )
        
        # Search for location
        result, matches = await location_service.find_location(query)
        
        if result is None:
            # No match and random points disabled
            return web.json_response(
                {
                    'found': False,
                    'query': query,
                    'matches': [],
                    'geometry': None,
                    'coordinates': None,
                },
                status=404
            )
        
        # Format response
        response_data = {
            'found': True,
            'query': query,
            'name': result.get('name', 'Unknown'),
            'coordinates': result.get('coordinates'),
            'geometry': result.get('geom'),
            'matches': matches,
            'strategy': result.get('strategy', 'unknown'),
        }
        
        return web.json_response(response_data)
        
    except Exception as e:
        logger.error(f"Location search failed: {e}", exc_info=True)
        return web.json_response(
            {'error': f'Location search failed: {str(e)}'},
            status=500
        )


async def post_location_search_handler(request: web.Request) -> web.Response:
    """
    Search for location by text query (POST method).
    
    Request Body (JSON):
        query (str): Search query text (required)
        
    Returns:
        JSON response with location data (same as GET handler)
    """
    try:
        # Parse JSON body
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {'error': 'Invalid JSON body'},
                status=400
            )
        
        query = data.get('query', '').strip()
        
        if not query:
            return web.json_response(
                {'error': 'Field "query" is required in JSON body'},
                status=400
            )
        
        if len(query) < 3:
            return web.json_response(
                {'error': 'Query must be at least 3 characters long'},
                status=400
            )
        
        # Get location service
        location_service = request.app.get('location_service')
        
        if not location_service:
            logger.error("LocationService not available")
            return web.json_response(
                {'error': 'Location service not available'},
                status=503
            )
        
        # Search for location
        result, matches = await location_service.find_location(query)
        
        if result is None:
            return web.json_response(
                {
                    'found': False,
                    'query': query,
                    'matches': [],
                    'geometry': None,
                    'coordinates': None,
                },
                status=404
            )
        
        # Format response
        response_data = {
            'found': True,
            'query': query,
            'name': result.get('name', 'Unknown'),
            'coordinates': result.get('coordinates'),
            'geometry': result.get('geom'),
            'matches': matches,
            'strategy': result.get('strategy', 'unknown'),
        }
        
        return web.json_response(response_data)
        
    except Exception as e:
        logger.error(f"Location search (POST) failed: {e}", exc_info=True)
        return web.json_response(
            {'error': f'Location search failed: {str(e)}'},
            status=500
        )


async def get_location_batch_search_handler(request: web.Request) -> web.Response:
    """
    Batch search for multiple locations.
    
    Request Body (JSON):
        queries (List[str]): List of search queries (required)
        
    Returns:
        JSON response with array of location results
    """
    try:
        # Parse JSON body
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {'error': 'Invalid JSON body'},
                status=400
            )
        
        queries = data.get('queries', [])
        
        if not queries or not isinstance(queries, list):
            return web.json_response(
                {'error': 'Field "queries" must be a non-empty array'},
                status=400
            )
        
        if len(queries) > 50:
            return web.json_response(
                {'error': 'Maximum 50 queries per request'},
                status=400
            )
        
        # Get location service
        location_service = request.app.get('location_service')
        
        if not location_service:
            return web.json_response(
                {'error': 'Location service not available'},
                status=503
            )
        
        # Process queries concurrently
        import asyncio
        
        async def process_query(query: str) -> Dict[str, Any]:
            """Process single query."""
            query = query.strip()
            
            if len(query) < 3:
                return {
                    'query': query,
                    'found': False,
                    'error': 'Query too short (min 3 chars)',
                }
            
            result, matches = await location_service.find_location(query)
            
            if result is None:
                return {
                    'query': query,
                    'found': False,
                    'matches': [],
                    'geometry': None,
                    'coordinates': None,
                }
            
            return {
                'query': query,
                'found': True,
                'name': result.get('name', 'Unknown'),
                'coordinates': result.get('coordinates'),
                'geometry': result.get('geom'),
                'matches': matches,
                'strategy': result.get('strategy', 'unknown'),
            }
        
        # Process all queries
        results = await asyncio.gather(*[
            process_query(q) for q in queries
        ])
        
        return web.json_response({
            'count': len(results),
            'results': results,
        })
        
    except Exception as e:
        logger.error(f"Batch location search failed: {e}", exc_info=True)
        return web.json_response(
            {'error': f'Batch search failed: {str(e)}'},
            status=500
        )
