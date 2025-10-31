"""Home Assistant service implementation for MCP integration via Nabu Casa or local connection"""

import os
import logging
import json
import requests
from services.cache import cache_aside, CacheConfig, CacheTTL
try:
    import websocket
except ImportError:
    # Try alternative import if websocket-client not found
    try:
        from websocket import WebSocket as websocket
    except ImportError:
        websocket = None
        logging.warning("websocket-client not available - WebSocket features disabled")
import ssl
from typing import Dict, List, Optional, Any, Union
from datetime import datetime, timezone, timedelta
from enum import Enum
from urllib.parse import urlparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP
    from services.cache import RedisCache

logger = logging.getLogger(__name__)


class ConnectionType(Enum):
    """Connection type to Home Assistant"""
    LOCAL = "local"
    NABU_CASA = "nabu_casa"


class Domain(Enum):
    """Home Assistant domains"""
    LIGHT = "light"
    SWITCH = "switch"
    CLIMATE = "climate"
    COVER = "cover"
    LOCK = "lock"
    MEDIA_PLAYER = "media_player"
    FAN = "fan"
    VACUUM = "vacuum"
    SCENE = "scene"
    SCRIPT = "script"
    AUTOMATION = "automation"
    INPUT_BOOLEAN = "input_boolean"
    INPUT_NUMBER = "input_number"
    INPUT_SELECT = "input_select"
    INPUT_TEXT = "input_text"
    INPUT_DATETIME = "input_datetime"
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    DEVICE_TRACKER = "device_tracker"
    PERSON = "person"
    ZONE = "zone"
    ALARM_CONTROL_PANEL = "alarm_control_panel"
    CAMERA = "camera"
    WEATHER = "weather"
    WATER_HEATER = "water_heater"
    HUMIDIFIER = "humidifier"
    AIR_QUALITY = "air_quality"
    GROUP = "group"
    REMOTE = "remote"
    SIREN = "siren"
    NOTIFY = "notify"


class HomeAssistantService:
    """Synchronous service for interacting with Home Assistant via REST API"""
    
    def __init__(self, url: str, access_token: str, verify_ssl: bool = True):
        """
        Initialize Home Assistant service
        
        Args:
            url: Home Assistant URL (local or Nabu Casa remote URL)
            access_token: Long-lived access token
            verify_ssl: Whether to verify SSL certificates
        """
        self.url = url.rstrip('/')
        self.access_token = access_token
        self.verify_ssl = verify_ssl
        self.connection_type = self._detect_connection_type()
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
        self.timeout = 30  # seconds
        self.areas_cache: Optional[List[Dict]] = None
        self.devices_cache: Optional[List[Dict]] = None
        self.entities_cache: Optional[Dict[str, Dict]] = None
        
        logger.info(f"Initialized Home Assistant service ({self.connection_type.value}): {self.url}")
    
    # ========== VALIDATION HELPERS ==========
    
    def _validate_entity_id(self, entity_id: Union[str, List[str]]) -> None:
        """Validate entity ID format and existence"""
        entity_ids = [entity_id] if isinstance(entity_id, str) else entity_id
        
        for eid in entity_ids:
            # Check format
            if '.' not in eid:
                raise ValueError(
                    f"Invalid entity_id format: '{eid}'.\n"
                    "Entity IDs must be in format 'domain.entity_name'.\n"
                    "Examples:\n"
                    "  • 'light.living_room'\n"
                    "  • 'switch.garage_door'\n"
                    "  • 'climate.thermostat'\n"
                    "To find valid entity IDs:\n"
                    "  • Use `get_ha_all_entities` to list all entities\n"
                    "  • Use `get_ha_devices_by_area` to find entities in specific areas"
                )
            
            domain = eid.split('.')[0]
            valid_domains = [d.value for d in Domain]
            if domain not in valid_domains:
                raise ValueError(
                    f"Invalid domain in entity_id '{eid}': '{domain}'.\n"
                    f"Valid domains: {', '.join(valid_domains[:10])}...\n"
                    "To find valid entity IDs:\n"
                    "  • Use `get_ha_all_entities` to list all entities\n"
                    "  • Use `get_ha_services` to list available domains and services"
                )
    
    def _validate_domain(self, domain: str) -> None:
        """Validate domain value"""
        valid_domains = [d.value for d in Domain]
        if domain not in valid_domains:
            raise ValueError(
                f"Invalid domain: '{domain}'.\n"
                f"Valid domains include: {', '.join(valid_domains[:15])}...\n"
                "To find available domains:\n"
                "  • Use `get_ha_services` to list all domains and their services\n"
                "  • Use `get_ha_all_entities` to see entities grouped by domain"
            )
    
    def _validate_service(self, domain: str, service: str) -> None:
        """Validate service exists for domain"""
        try:
            services = self.get_services()
            if domain in services:
                if service not in services[domain]['services']:
                    available = list(services[domain]['services'].keys())
                    raise ValueError(
                        f"Invalid service '{service}' for domain '{domain}'.\n"
                        f"Available services: {', '.join(available[:10])}\n"
                        "To see all services:\n"
                        f"  • Use `get_ha_services` to list services for {domain} domain"
                    )
        except Exception as e:
            # If we can't validate, log warning but don't block
            logger.warning(f"Could not validate service {domain}.{service}: {e}")
    
    def _validate_brightness(self, brightness: Optional[int]) -> Optional[int]:
        """Validate brightness value and return as integer"""
        if brightness is not None:
            # Convert to int if string
            try:
                brightness_int = int(brightness)
            except (ValueError, TypeError):
                raise ValueError(
                    f"Invalid brightness value: {brightness}. Brightness must be an integer between 0-255.\n"
                    "Examples:\n"
                    "  • 0 = Off (minimum)\n"
                    "  • 128 = 50% brightness\n"
                    "  • 255 = Full brightness (maximum)"
                )
            
            if brightness_int < 0 or brightness_int > 255:
                raise ValueError(
                    f"Invalid brightness value: {brightness_int}.\n"
                    "Brightness must be between 0 and 255.\n"
                    "Examples:\n"
                    "  • 0 = Off (minimum)\n"
                    "  • 128 = 50% brightness\n"
                    "  • 255 = Full brightness (maximum)"
                )
            return brightness_int
        return None
    
    def _validate_temperature(self, temperature: Optional[float], unit: str = "C") -> None:
        """Validate temperature value"""
        if temperature is not None:
            if unit == "C":
                if temperature < -50 or temperature > 50:
                    raise ValueError(
                        f"Invalid temperature: {temperature}°C.\n"
                        "Temperature should be between -50°C and 50°C for most climates.\n"
                        "Common settings:\n"
                        "  • 20-22°C = Comfortable room temperature\n"
                        "  • 16-18°C = Sleeping temperature\n"
                        "  • 23-26°C = Warm setting"
                    )
            elif unit == "F":
                if temperature < -58 or temperature > 122:
                    raise ValueError(
                        f"Invalid temperature: {temperature}°F.\n"
                        "Temperature should be between -58°F and 122°F for most climates.\n"
                        "Common settings:\n"
                        "  • 68-72°F = Comfortable room temperature\n"
                        "  • 60-65°F = Sleeping temperature\n"
                        "  • 73-79°F = Warm setting"
                    )
    
    def _validate_hvac_mode(self, hvac_mode: Optional[str]) -> None:
        """Validate HVAC mode"""
        valid_modes = ['off', 'heat', 'cool', 'heat_cool', 'auto', 'dry', 'fan_only']
        if hvac_mode is not None and hvac_mode not in valid_modes:
            raise ValueError(
                f"Invalid HVAC mode: '{hvac_mode}'.\n"
                f"Valid modes: {', '.join(valid_modes)}\n"
                "Mode descriptions:\n"
                "  • 'off' - System off\n"
                "  • 'heat' - Heating mode\n"
                "  • 'cool' - Cooling mode\n"
                "  • 'heat_cool' - Auto heat/cool\n"
                "  • 'auto' - Automatic mode\n"
                "  • 'dry' - Dehumidify mode\n"
                "  • 'fan_only' - Fan only, no heating/cooling"
            )
    
    def _validate_area(self, area: str) -> None:
        """Validate area exists"""
        try:
            areas = self.get_areas()
            area_names = [a['name'].lower() for a in areas]
            if area.lower() not in area_names:
                raise ValueError(
                    f"Invalid area: '{area}'.\n"
                    f"Available areas: {', '.join([a['name'] for a in areas])}\n"
                    "To manage areas:\n"
                    "  • Use `get_ha_areas` to list all areas\n"
                    "  • Areas are configured in Home Assistant UI"
                )
        except Exception as e:
            logger.warning(f"Could not validate area {area}: {e}")
    
    def _detect_connection_type(self) -> ConnectionType:
        """Detect if this is a local or Nabu Casa connection"""
        parsed = urlparse(self.url)
        if 'ui.nabu.casa' in parsed.netloc or 'remote.nabucasa.com' in parsed.netloc:
            return ConnectionType.NABU_CASA
        return ConnectionType.LOCAL
    
    def test_connection(self) -> Dict[str, Any]:
        """Test connection to Home Assistant"""
        try:
            response = requests.get(
                f"{self.url}/api/",
                headers=self.headers,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            if response.status_code == 200:
                data = response.json()
                return {
                    "status": "success",
                    "message": data.get("message", "API running."),
                    "connection_type": self.connection_type.value,
                    "url": self.url
                }
            else:
                return {
                    "status": "error",
                    "error": f"HTTP {response.status_code}",
                    "connection_type": self.connection_type.value,
                    "url": self.url
                }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "connection_type": self.connection_type.value,
                "url": self.url
            }
    
    def get_config(self) -> Dict[str, Any]:
        """Get Home Assistant configuration"""
        response = requests.get(
            f"{self.url}/api/config",
            headers=self.headers,
            verify=self.verify_ssl,
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()
    
    def get_states(self, entity_ids: Optional[List[str]] = None,
                  domain: Optional[str] = None,
                  area: Optional[str] = None,
                  limit: Optional[int] = None,
                  offset: int = 0) -> List[Dict[str, Any]]:
        """
        Get states of entities with optional filtering and pagination
        
        Args:
            entity_ids: List of specific entity IDs to fetch
            domain: Filter by domain (e.g., 'light', 'switch')
            area: Filter by area name
            limit: Maximum number of results to return (for pagination)
            offset: Number of results to skip (for pagination)
        
        Returns:
            List of entity states
        """
        try:
            response = requests.get(
                f"{self.url}/api/states",
                headers=self.headers,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            response.raise_for_status()
            states = response.json()
        except Exception as e:
            logger.error(f"Failed to get states: {e}")
            raise ValueError(
                f"Failed to retrieve Home Assistant states: {str(e)}\n"
                "To reduce response size:\n"
                "  • Use domain parameter to filter by type (e.g., 'light', 'switch')\n"
                "  • Use area parameter to filter by room/area\n"
                "  • Use limit parameter to paginate results\n"
                "  • Use specific entity_ids to get only what you need"
            )
        
        # Apply filters
        if entity_ids:
            states = [s for s in states if s['entity_id'] in entity_ids]
        
        if domain:
            states = [s for s in states if s['entity_id'].startswith(f"{domain}.")]
        
        if area:
            # Get areas first if filtering by area
            areas = self.get_areas()
            area_ids = [a.get('area_id', a.get('id')) for a in areas 
                       if a.get('name', '').lower() == area.lower()]
            
            if area_ids:
                # Get entities for the area
                entities = self.get_entities()
                area_entity_ids = [
                    e['entity_id'] for e in entities 
                    if e.get('area_id') in area_ids
                ]
                states = [s for s in states if s['entity_id'] in area_entity_ids]
            else:
                states = []  # Area not found
        
        # Apply pagination
        if limit is not None:
            end_index = offset + limit
            states = states[offset:end_index]
        elif offset > 0:
            states = states[offset:]
        
        # Check if response is too large (> 900KB to leave room for wrapper)
        import json
        response_size = len(json.dumps(states))
        if response_size > 900000:  # ~900KB
            truncated_states = states[:100]  # Return first 100 states
            logger.warning(f"Response too large ({response_size} bytes), truncating to 100 states")
            return truncated_states + [{
                "entity_id": "_truncated",
                "state": "warning",
                "attributes": {
                    "message": f"Response truncated: {len(states)} total states exceed size limit",
                    "total_states": len(states),
                    "returned_states": 100,
                    "help": "Use domain, area, or entity_ids filters to reduce response size"
                }
            }]
        
        return states
    
    def call_service(self, domain: str, service: str,
                    entity_id: Optional[Union[str, List[str]]] = None,
                    **service_data) -> Dict[str, Any]:
        """
        Call a Home Assistant service
        
        Args:
            domain: Service domain (e.g., 'light', 'switch')
            service: Service name (e.g., 'turn_on', 'turn_off')
            entity_id: Entity ID(s) to target
            **service_data: Additional service data
        
        Returns:
            Service call result
        """
        # Validate parameters
        if entity_id:
            self._validate_entity_id(entity_id)
        self._validate_domain(domain)
        self._validate_service(domain, service)
        
        data = service_data.copy()
        
        if entity_id:
            if isinstance(entity_id, list):
                data['entity_id'] = entity_id
            else:
                data['entity_id'] = entity_id
        
        try:
            response = requests.post(
                f"{self.url}/api/services/{domain}/{service}",
                headers=self.headers,
                json=data,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                return {"status": "success", "domain": domain, "service": service}
            elif response.status_code == 401:
                raise ValueError(
                    "Authentication failed with Home Assistant.\n"
                    "To fix:\n"
                    "  • Check your HA_TOKEN is valid\n"
                    "  • Generate a new token in Home Assistant: Profile → Long-Lived Access Tokens\n"
                    "  • Ensure the token has not expired"
                )
            elif response.status_code == 404:
                raise ValueError(
                    f"Service '{domain}.{service}' not found in Home Assistant.\n"
                    "To fix:\n"
                    "  • Use `get_ha_services` to see available services\n"
                    "  • Check if the integration for this domain is installed\n"
                    "  • Verify the service name spelling"
                )
            else:
                return {
                    "status": "error",
                    "error": f"HTTP {response.status_code}: {response.text}",
                    "domain": domain,
                    "service": service,
                    "help": "Use `get_ha_services` to see available services"
                }
        except requests.exceptions.Timeout:
            raise ValueError(
                f"Connection to Home Assistant timed out after {self.timeout} seconds.\n"
                "Possible issues:\n"
                "  • Home Assistant is not running\n"
                "  • Network connectivity issues\n"
                "  • Incorrect HA_URL configuration\n"
                f"  • Current URL: {self.url}\n"
                "To diagnose:\n"
                "  • Use `get_ha_connection_status` to test the connection\n"
                "  • Check if you can access Home Assistant in a browser\n"
                "  • Verify firewall/port settings"
            )
        except requests.exceptions.ConnectionError as e:
            raise ValueError(
                f"Cannot connect to Home Assistant at {self.url}.\n"
                "Possible issues:\n"
                "  • Home Assistant is not running\n"
                "  • Wrong URL or port number\n"
                "  • Firewall blocking the connection\n"
                "To fix:\n"
                "  • Verify HA_URL environment variable\n"
                "  • Check Home Assistant is running\n"
                "  • Try using local URL if using Nabu Casa\n"
                f"Original error: {str(e)}"
            )
        except ValueError:
            # Re-raise validation errors
            raise
        except Exception as e:
            raise ValueError(
                f"Unexpected error calling {domain}.{service}: {str(e)}\n"
                "For help:\n"
                "  • Use `get_ha_services` to verify the service exists\n"
                "  • Use `get_ha_connection_status` to test connectivity\n"
                "  • Check Home Assistant logs for more details"
            )
    
    def turn_on(self, entity_id: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """Turn on an entity"""
        # Validate entity_id first
        self._validate_entity_id(entity_id)
        
        # Validate common parameters if provided
        if 'brightness' in kwargs:
            self._validate_brightness(kwargs['brightness'])
        
        domain = entity_id.split('.')[0] if isinstance(entity_id, str) else entity_id[0].split('.')[0]
        return self.call_service(domain, "turn_on", entity_id=entity_id, **kwargs)
    
    def turn_off(self, entity_id: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """Turn off an entity"""
        # Validate entity_id first
        self._validate_entity_id(entity_id)
        
        domain = entity_id.split('.')[0] if isinstance(entity_id, str) else entity_id[0].split('.')[0]
        return self.call_service(domain, "turn_off", entity_id=entity_id, **kwargs)
    
    def toggle(self, entity_id: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """Toggle an entity"""
        # Validate entity_id first
        self._validate_entity_id(entity_id)
        
        domain = entity_id.split('.')[0] if isinstance(entity_id, str) else entity_id[0].split('.')[0]
        return self.call_service(domain, "toggle", entity_id=entity_id, **kwargs)
    
    def set_value(self, entity_id: str, value: Any) -> Dict[str, Any]:
        """Set value for an input entity"""
        domain = entity_id.split('.')[0]
        
        if domain == "input_number":
            return self.call_service(domain, "set_value", entity_id=entity_id, value=float(value))
        elif domain == "input_text":
            return self.call_service(domain, "set_value", entity_id=entity_id, value=str(value))
        elif domain == "input_select":
            return self.call_service(domain, "select_option", entity_id=entity_id, option=str(value))
        elif domain == "input_boolean":
            service = "turn_on" if value else "turn_off"
            return self.call_service(domain, service, entity_id=entity_id)
        elif domain == "input_datetime":
            if isinstance(value, str):
                return self.call_service(domain, "set_datetime", entity_id=entity_id, datetime=value)
            elif isinstance(value, dict):
                return self.call_service(domain, "set_datetime", entity_id=entity_id, **value)
        else:
            return {"status": "error", "error": f"Unsupported domain for set_value: {domain}"}
    
    def activate_scene(self, scene_id: str) -> Dict[str, Any]:
        """Activate a scene"""
        return self.call_service("scene", "turn_on", entity_id=scene_id)
    
    def run_script(self, script_id: str, **variables) -> Dict[str, Any]:
        """Run a script with optional variables"""
        return self.call_service("script", "turn_on", entity_id=script_id, variables=variables)
    
    def trigger_automation(self, automation_id: str) -> Dict[str, Any]:
        """Manually trigger an automation"""
        return self.call_service("automation", "trigger", entity_id=automation_id)
    
    def send_notification(self, message: str, title: Optional[str] = None,
                         service_name: str = "notify", **kwargs) -> Dict[str, Any]:
        """Send a notification"""
        data = {"message": message}
        if title:
            data["title"] = title
        data.update(kwargs)
        return self.call_service("notify", service_name, **data)
    
    def get_areas(self, minimal: bool = True) -> List[Dict[str, Any]]:
        """Get all areas/rooms via WebSocket API
        
        Args:
            minimal: If True, return only essential fields to reduce token usage
        """
        if self.areas_cache is not None:
            if minimal:
                # Return minimal area data for LLM consumption
                return [{
                    'id': a.get('area_id'),
                    'name': a.get('name'),
                    'floor': a.get('floor_id')
                } for a in self.areas_cache]
            return self.areas_cache
        
        try:
            # Try REST API first for backward compatibility
            response = requests.get(
                f"{self.url}/api/config/area_registry/list",
                headers=self.headers,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            
            if response.status_code == 200:
                self.areas_cache = response.json()
                if minimal:
                    return [{
                        'id': a.get('area_id'),
                        'name': a.get('name'),
                        'floor': a.get('floor_id')
                    } for a in self.areas_cache]
                return self.areas_cache
            
            # REST API not available, use WebSocket
            logger.info("REST API for areas not available, using WebSocket API")
            areas = self._get_areas_via_websocket()
            
            if areas is not None:
                self.areas_cache = areas
                if minimal:
                    return [{
                        'id': a.get('area_id'),
                        'name': a.get('name'),
                        'floor': a.get('floor_id')
                    } for a in self.areas_cache]
                return self.areas_cache
            else:
                # WebSocket also failed, return helpful message
                logger.info("Could not retrieve areas via WebSocket")
                return [{
                    "id": "areas_not_available",
                    "name": "Areas Not Available",
                    "message": "Could not retrieve areas from Home Assistant.",
                    "suggestions": [
                        "Check Home Assistant connection",
                        "Verify authentication token",
                        "Configure areas in Home Assistant UI under Settings > Areas & Zones"
                    ],
                    "note": "You can still control devices using their entity IDs"
                }]
                
        except Exception as e:
            logger.error(f"Failed to get areas: {e}")
            # Try WebSocket as fallback
            areas = self._get_areas_via_websocket()
            if areas is not None:
                self.areas_cache = areas
                if minimal:
                    return [{
                        'id': a.get('area_id'),
                        'name': a.get('name'),
                        'floor': a.get('floor_id')
                    } for a in self.areas_cache]
                return self.areas_cache
            return []
    
    def _get_areas_via_websocket(self) -> Optional[List[Dict[str, Any]]]:
        """Get areas via WebSocket API when REST is not available"""
        if websocket is None:
            logger.warning("WebSocket support not available - websocket-client not installed")
            return None
        try:
            # Convert HTTP URL to WebSocket URL
            ws_url = self.url.replace('http://', 'ws://').replace('https://', 'wss://')
            ws_url = f"{ws_url}/api/websocket"
            
            # Create SSL context that doesn't verify certificates if needed
            sslopt = {"cert_reqs": ssl.CERT_NONE} if not self.verify_ssl else None
            
            # Create WebSocket connection
            ws = websocket.create_connection(ws_url, sslopt=sslopt, timeout=self.timeout)
            
            try:
                # Wait for auth_required message
                auth_required = ws.recv()
                auth_data = json.loads(auth_required)
                
                if auth_data.get('type') != 'auth_required':
                    logger.error(f"Unexpected initial message: {auth_data}")
                    return None
                
                # Send authentication
                auth_msg = {
                    "type": "auth",
                    "access_token": self.access_token
                }
                ws.send(json.dumps(auth_msg))
                
                # Wait for auth result
                auth_result = ws.recv()
                result = json.loads(auth_result)
                
                if result.get('type') != 'auth_ok':
                    logger.error(f"WebSocket authentication failed: {result}")
                    return None
                
                # Request area registry list
                area_request = {
                    "id": 1,
                    "type": "config/area_registry/list"
                }
                ws.send(json.dumps(area_request))
                
                # Get response
                response = ws.recv()
                data = json.loads(response)
                
                if data.get('success'):
                    areas = data.get('result', [])
                    logger.info(f"Successfully retrieved {len(areas)} areas via WebSocket")
                    return areas
                else:
                    error = data.get('error', {})
                    logger.error(f"WebSocket area request failed: {error.get('message', 'Unknown error')}")
                    return None
                    
            finally:
                ws.close()
                
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            return None
    
    def get_devices(self, minimal: bool = True, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get all devices from states
        
        Args:
            minimal: If True, return only essential fields to reduce token usage
            limit: Maximum number of results to return (for pagination)
            offset: Number of results to skip (for pagination)
        """
        if self.devices_cache is not None:
            devices = self.devices_cache
            
            # Apply pagination
            if limit is not None:
                end_index = offset + limit
                devices = devices[offset:end_index]
            elif offset > 0:
                devices = devices[offset:]
            
            if minimal:
                # Return only essential fields for LLM consumption
                return [{
                    'id': d['id'],
                    'name': d['name'],
                    'area_id': d.get('area_id'),
                    'manufacturer': d.get('manufacturer'),
                    'model': d.get('model'),
                    'entities': d.get('entities', [])
                } for d in devices]
            return devices
        
        try:
            # Try the device registry endpoint first
            response = requests.get(
                f"{self.url}/api/config/device_registry/list",
                headers=self.headers,
                verify=self.verify_ssl,
                timeout=self.timeout
            )
            response.raise_for_status()
            self.devices_cache = response.json()
            devices = self.devices_cache
            
            # Apply pagination
            if limit is not None:
                end_index = offset + limit
                devices = devices[offset:end_index]
            elif offset > 0:
                devices = devices[offset:]
            
            if minimal:
                return [{
                    'id': d.get('id'),
                    'name': d.get('name'),
                    'area_id': d.get('area_id'),
                    'manufacturer': d.get('manufacturer'),
                    'model': d.get('model'),
                    'entities': d.get('entities', [])
                } for d in devices]
            return devices
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                # Fallback: Extract device METADATA from states (not current values)
                states = self.get_states()
                devices = {}
                
                for state in states:
                    # Extract device info from attributes if available
                    attrs = state.get('attributes', {})
                    device_id = attrs.get('device_id')
                    
                    if device_id and device_id not in devices:
                        devices[device_id] = {
                            'id': device_id,
                            'name': attrs.get('device_name', f"Device {device_id}"),
                            'manufacturer': attrs.get('manufacturer'),
                            'model': attrs.get('model'),
                            'sw_version': attrs.get('sw_version'),
                            'hw_version': attrs.get('hw_version'),
                            'area_id': attrs.get('area_id'),
                            'via_device_id': attrs.get('via_device_id'),
                            'entities': []
                        }
                    
                    if device_id:
                        devices[device_id]['entities'].append(state.get('entity_id'))
                
                # If no devices found from states, return empty list with message
                if not devices:
                    logger.info("Device registry not available through Nabu Casa, and no device info in states")
                    return []
                
                self.devices_cache = list(devices.values())
                devices_list = self.devices_cache
                
                # Apply pagination
                if limit is not None:
                    end_index = offset + limit
                    devices_list = devices_list[offset:end_index]
                elif offset > 0:
                    devices_list = devices_list[offset:]
                
                if minimal:
                    return [{
                        'id': d['id'],
                        'name': d['name'],
                        'area_id': d.get('area_id'),
                        'manufacturer': d.get('manufacturer'),
                        'model': d.get('model'),
                        'entities': d.get('entities', [])
                    } for d in devices_list]
                return devices_list
            else:
                raise
    
    def get_entities(self, minimal: bool = True, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get all entities metadata (without current states)
        
        Args:
            minimal: If True, return only essential fields to reduce token usage
            limit: Maximum number of results to return (for pagination)
            offset: Number of results to skip (for pagination)
        """
        if self.entities_cache is not None:
            entities = list(self.entities_cache.values())
            
            # Apply pagination
            if limit is not None:
                end_index = offset + limit
                entities = entities[offset:end_index]
            elif offset > 0:
                entities = entities[offset:]
            
            if minimal:
                # Return only essential fields for LLM consumption
                return [{
                    'entity_id': e['entity_id'],
                    'name': e['name'],
                    'domain': e['domain'],
                    'area_id': e.get('area_id'),
                    'device_class': e.get('device_class')  # Keep device_class as it's useful for understanding entity type
                } for e in entities]
            return entities
        
        # Get all states first
        states = self.get_states()
        
        # Extract entity METADATA only (not current states)
        entities = []
        for state in states:
            attrs = state.get('attributes', {})
            entity_info = {
                'entity_id': state.get('entity_id'),
                'name': attrs.get('friendly_name', state.get('entity_id')),
                'domain': state.get('entity_id', '').split('.')[0] if '.' in state.get('entity_id', '') else 'unknown',
                # Include only metadata attributes, not state
                'device_class': attrs.get('device_class'),
                'unit_of_measurement': attrs.get('unit_of_measurement'),
                'icon': attrs.get('icon'),
                'area_id': attrs.get('area_id'),
                'device_id': attrs.get('device_id'),
                'hidden': attrs.get('hidden', False),
                'disabled': attrs.get('disabled', False)
            }
            entities.append(entity_info)
        
        self.entities_cache = {e['entity_id']: e for e in entities}
        
        # Apply pagination
        if limit is not None:
            end_index = offset + limit
            entities = entities[offset:end_index]
        elif offset > 0:
            entities = entities[offset:]
        
        if minimal:
            return [{
                'entity_id': e['entity_id'],
                'name': e['name'],
                'domain': e['domain'],
                'area_id': e.get('area_id'),
                'device_class': e.get('device_class')
            } for e in entities]
        
        return entities
    
    def get_services(self) -> Dict[str, Any]:
        """Get all available services"""
        response = requests.get(
            f"{self.url}/api/services",
            headers=self.headers,
            verify=self.verify_ssl,
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()
    
    def get_history(self, entity_id: str, start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None, limit: Optional[int] = None,
                   offset: int = 0) -> List[Dict[str, Any]]:
        """
        Get history for an entity
        
        Args:
            entity_id: Entity ID to get history for
            start_time: Start time (defaults to 24 hours ago)
            end_time: End time (defaults to now)
            limit: Maximum number of results to return (for pagination)
            offset: Number of results to skip (for pagination)
        
        Returns:
            List of historical states
        """
        if not start_time:
            start_time = datetime.now(timezone.utc) - timedelta(days=1)
        if not end_time:
            end_time = datetime.now(timezone.utc)
        
        params = {
            "filter_entity_id": entity_id,
            "minimal_response": "false",
            "no_attributes": "false"
        }
        
        url = f"{self.url}/api/history/period/{start_time.isoformat()}"
        if end_time:
            params["end_time"] = end_time.isoformat()
        
        response = requests.get(
            url,
            headers=self.headers,
            params=params,
            verify=self.verify_ssl,
            timeout=self.timeout
        )
        response.raise_for_status()
        history = response.json()
        
        # History API returns a list of lists, we want the first one for single entity
        result = history[0] if history else []
        
        # Apply pagination
        if limit is not None:
            end_index = offset + limit
            result = result[offset:end_index]
        elif offset > 0:
            result = result[offset:]
        
        return result
    
    def get_logbook(self, entity_id: Optional[str] = None,
                   start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """
        Get logbook entries
        
        Args:
            entity_id: Optional entity ID to filter by
            start_time: Start time (defaults to 24 hours ago)
            end_time: End time (defaults to now)
        
        Returns:
            List of logbook entries
        """
        if not start_time:
            start_time = datetime.now(timezone.utc) - timedelta(days=1)
        if not end_time:
            end_time = datetime.now(timezone.utc)
        
        params = {}
        if entity_id:
            params["entity"] = entity_id
        if end_time:
            params["end_time"] = end_time.isoformat()
        
        url = f"{self.url}/api/logbook/{start_time.isoformat()}"
        
        response = requests.get(
            url,
            headers=self.headers,
            params=params,
            verify=self.verify_ssl,
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()
    
    def fire_event(self, event_type: str, event_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Fire a custom event
        
        Args:
            event_type: Event type to fire
            event_data: Optional event data
        
        Returns:
            Result of event firing
        """
        response = requests.post(
            f"{self.url}/api/events/{event_type}",
            headers=self.headers,
            json=event_data or {},
            verify=self.verify_ssl,
            timeout=self.timeout
        )
        
        if response.status_code == 200:
            return {"status": "success", "event_type": event_type}
        else:
            return {
                "status": "error",
                "error": f"HTTP {response.status_code}: {response.text}",
                "event_type": event_type
            }
    
    def set_state(self, entity_id: str, state: str, attributes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Set state of an entity (use with caution)
        
        Args:
            entity_id: Entity ID
            state: New state
            attributes: Optional attributes
        
        Returns:
            New state object
        """
        data = {
            "state": state
        }
        if attributes:
            data["attributes"] = attributes
        
        response = requests.post(
            f"{self.url}/api/states/{entity_id}",
            headers=self.headers,
            json=data,
            verify=self.verify_ssl,
            timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()


class HomeAssistantClient:
    """Synchronous client wrapper for Home Assistant integration"""
    
    def __init__(self, 
                 url: Optional[str] = None, 
                 access_token: Optional[str] = None,
                 verify_ssl: bool = True,
                 mcp: Optional['FastMCP'] = None,
                 cache: Optional['RedisCache'] = None):
        """
        Initialize Home Assistant client
        
        Args:
            url: Home Assistant URL (or from HA_URL env var)
            access_token: Long-lived access token (or from HA_TOKEN env var)
            verify_ssl: Whether to verify SSL certificates
            mcp: FastMCP instance for tool registration
            cache: Redis cache instance for caching responses
        """
        self.url = url or os.getenv('HA_URL')
        self.access_token = access_token or os.getenv('HA_TOKEN')
        
        if not self.url or not self.access_token:
            raise ValueError("Home Assistant URL and access token are required")
        
        self.service = HomeAssistantService(self.url, self.access_token, verify_ssl)
        self.mcp = mcp
        self.cache = cache
        
        # Register MCP tools if MCP server is provided
        if self.mcp:
            self._register_mcp_tools()
    
    def test_connection(self) -> Dict[str, Any]:
        """Test connection to Home Assistant"""
        return self.service.test_connection()
    
    def get_config(self) -> Dict[str, Any]:
        """Get Home Assistant configuration"""
        return self.service.get_config()
    
    @cache_aside(CacheConfig(ttl=CacheTTL.HA_STATES, key_prefix="ha:states"))
    def get_states(self, entity_ids: Optional[List[str]] = None,
                  domain: Optional[str] = None,
                  area: Optional[str] = None,
                  limit: Optional[int] = None,
                  offset: int = 0) -> Dict[str, Any]:
        """Get entity states with optional pagination"""
        try:
            states = self.service.get_states(entity_ids, domain, area, limit, offset)
            return {"states": states, "count": len(states)}
        except Exception as e:
            return {"error": str(e), "states": [], "count": 0}
    
    def call_service(self, domain: str, service: str,
                    entity_id: Optional[Union[str, List[str]]] = None,
                    **service_data) -> Dict[str, Any]:
        """Call Home Assistant service"""
        return self.service.call_service(domain, service, entity_id, **service_data)
    
    def turn_on(self, entity_id: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """Turn on an entity"""
        return self.service.turn_on(entity_id, **kwargs)
    
    def turn_off(self, entity_id: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """Turn off an entity"""
        return self.service.turn_off(entity_id, **kwargs)
    
    def toggle(self, entity_id: Union[str, List[str]], **kwargs) -> Dict[str, Any]:
        """Toggle an entity"""
        return self.service.toggle(entity_id, **kwargs)
    
    def set_value(self, entity_id: str, value: Any) -> Dict[str, Any]:
        """Set value for an input entity"""
        return self.service.set_value(entity_id, value)
    
    def activate_scene(self, scene_id: str) -> Dict[str, Any]:
        """Activate a scene"""
        return self.service.activate_scene(scene_id)
    
    def run_script(self, script_id: str, **variables) -> Dict[str, Any]:
        """Run a script"""
        return self.service.run_script(script_id, **variables)
    
    def trigger_automation(self, automation_id: str) -> Dict[str, Any]:
        """Trigger an automation"""
        return self.service.trigger_automation(automation_id)
    
    def send_notification(self, message: str, title: Optional[str] = None,
                         service_name: str = "notify", **kwargs) -> Dict[str, Any]:
        """Send a notification"""
        return self.service.send_notification(message, title, service_name, **kwargs)
    
    @cache_aside(CacheConfig(ttl=CacheTTL.HA_AREAS, key_prefix="ha:areas"))
    def get_areas(self) -> List[Dict[str, Any]]:
        """Get all areas"""
        return self.service.get_areas()
    
    @cache_aside(CacheConfig(ttl=CacheTTL.HA_DEVICE_LIST, key_prefix="ha:device_list"))
    def get_devices(self, minimal: bool = True, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get list of all devices (metadata only, not current states)"""
        # This returns device metadata, not their current states
        return self.service.get_devices(minimal, limit, offset)
    
    @cache_aside(CacheConfig(ttl=CacheTTL.HA_ENTITY_LIST, key_prefix="ha:entity_list"))
    def get_entities(self, minimal: bool = True, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get list of all entities (metadata only, current states use get_states)"""
        # This returns entity metadata, not their current states
        return self.service.get_entities(minimal, limit, offset)
    
    @cache_aside(CacheConfig(ttl=CacheTTL.HA_SERVICES, key_prefix="ha:services"))
    def get_services(self) -> Dict[str, Any]:
        """Get all services"""
        return self.service.get_services()
    
    def get_history(self, entity_id: str, start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None, limit: Optional[int] = None,
                   offset: int = 0) -> List[Dict[str, Any]]:
        """Get entity history with optional pagination"""
        return self.service.get_history(entity_id, start_time, end_time, limit, offset)
    
    def get_logbook(self, entity_id: Optional[str] = None,
                   start_time: Optional[datetime] = None,
                   end_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Get logbook entries"""
        return self.service.get_logbook(entity_id, start_time, end_time)
    
    def fire_event(self, event_type: str, event_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Fire an event"""
        return self.service.fire_event(event_type, event_data)
    
    def set_state(self, entity_id: str, state: str, attributes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Set entity state"""
        return self.service.set_state(entity_id, state, attributes)
    
    def _register_mcp_tools(self):
        """Register MCP tools for this service"""
        # NOTE: Getter tools commented out - use resources instead for read-only data
        # Resources provide: states, areas, devices, entities, services
        # Commented tools: ha_get_states, ha_get_areas
        
        # State tools
        # Commented out - use resources instead for read-only data
        # self.mcp.tool(
        #     name="ha_get_states",
        #     description="Retrieve current states of Home Assistant entities. Parameters: entity_id (specific entity or pattern with wildcards), domain (filter by type: light, switch, sensor, etc.), area (filter by room/area name). Returns: Array of entity states with id, state, attributes, last_changed, last_updated. Use to: Check device status, read sensor values, monitor system state, or find entities by area/type.",
        #     annotations={"title": "Get Home Assistant States"}
        # )(self.get_states)
        
        # Control tools
        self.mcp.tool(
            name="turn_on_device",
            description="""Turn on a Home Assistant device to its default/full state.

## Parameters
• entity_id: Entity to turn on (required)
  - Call `get_ha_entities` or `get_ha_states` to see available entities

## Returns
Success status with entity state

## Use Cases
• Turn on lights (full brightness)
• Activate switches
• Start fans
• Turn on any device to its default "on" state

## Related Tools
• Use `set_light_level` to set specific brightness (0-100%)
• Use `turn_off_device` to turn off
• Use `toggle_device` to switch state
• Use `get_ha_lights_on` to see currently on lights""",
            title="Turn On Device",
            annotations={"title": "Turn On Device"}
        )(self.turn_on_for_mcp)
        
        self.mcp.tool(
            name="turn_off_device",
            description="""Turn off a Home Assistant device completely.

## Parameters
• entity_id: Entity to turn off (required)
  - Call `get_ha_entities` or `get_ha_states` to see available entities

## Returns
Success status with entity state

## Use Cases
• Turn off lights completely
• Deactivate switches
• Stop fans
• Turn off any device

## Related Tools
• Use `set_light_level` to dim lights instead of turning off
• Use `turn_on_device` to turn on
• Use `toggle_device` to switch state
• Use `get_ha_devices_on` to see what's currently on""",
            title="Turn Off Device",
            annotations={"title": "Turn Off Device"}
        )(self.turn_off_for_mcp)
        
        self.mcp.tool(
            name="toggle_device",
            description="""Toggle a Home Assistant device between on and off states.

## Parameters
• entity_id: Entity to toggle (required)
  - Call `get_ha_entities` to see available entities

## Returns
Success status with new entity state

## Use Cases
• Switch device state without checking current status
• Quick on/off switching
• Works with lights, switches, fans""",
            title="Toggle Device",
            annotations={"title": "Toggle Device"}
        )(self.toggle_for_mcp)
        
        self.mcp.tool(
            name="set_light_level",
            description="""Set a light to a specific brightness level using percentage.

## Parameters
• entity_id: Light entity to control (required)
  - Call `get_ha_lights` to see available lights
• brightness_percent: 0-100 percentage (required)
  - 0 = completely off
  - 10 = very dim
  - 25 = dim mood lighting
  - 50 = half brightness
  - 75 = bright
  - 100 = full brightness

## Returns
Success status with light state

## Use Cases
• Set mood lighting (20-30%)
• Movie watching (10-15%)
• Reading light (60-75%)
• Working light (80-100%)
• Night light (5-10%)

## Related Tools
• Use `turn_on_device` for simple on (full brightness)
• Use `turn_off_device` for simple off
• Use `toggle_device` to switch state
• Use `get_ha_lights_on` to see current light states""",
            title="Set Light Level",
            annotations={"title": "Set Light Level"}
        )(self.set_light_level_for_mcp)
        
        # Climate control
        self.mcp.tool(
            name="set_climate_control",
            description="""Control climate/thermostat settings.

## Parameters
• entity_id: Climate entity (required)
  - Call `get_ha_climate_status` to see climate entities
• temperature: Target temperature (optional)
• target_temp_high/low: For dual setpoint systems (optional)
• hvac_mode: heat/cool/auto/off (optional)
• fan_mode: auto/low/medium/high (optional)
• preset_mode: away/eco/comfort (optional)

## Returns
Success status with climate entity state

## Use Cases
• Adjust temperature settings
• Change HVAC modes
• Activate comfort presets""",
            title="Set Climate Control",
            annotations={"title": "Set Climate Control"}
        )(self.set_climate_for_mcp)
        
        # Cover control
        self.mcp.tool(
            name="control_cover",
            description="""Control covers, blinds, shades, or garage doors.

## Parameters
• entity_id: Cover entity (required)
  - Call `get_ha_entities` with domain 'cover' to see available covers
• action: open/close/stop/set_position (required)
• position: 0-100 for partial opening (optional)

## Returns
Success status with cover state

## Use Cases
• Open/close window blinds
• Control garage doors
• Set shade positions""",
            title="Control Cover/Blind",
            annotations={"title": "Control Cover/Blind"}
        )(self.control_cover_for_mcp)
        
        # Lock control
        self.mcp.tool(
            name="control_lock",
            description="""Control smart locks.

## Parameters
• entity_id: Lock entity (required)
  - Call `get_ha_security_status` to see lock entities
• action: lock/unlock/open (required)
  - 'open' temporarily unlocks then re-locks

## Returns
Success status with lock state

## Use Cases
• Lock/unlock doors
• Grant temporary access
• Secure home remotely""",
            title="Control Lock",
            annotations={"title": "Control Lock"}
        )(self.lock_control_for_mcp)
        
        # Scene and automation
        self.mcp.tool(
            name="activate_scene",
            description="""Activate a predefined Home Assistant scene.

## Parameters
• scene_id: Scene entity ID, format: scene.name (required)
  - Call `get_ha_scenes` to see available scenes

## Returns
Success status

## Use Cases
• Set mood lighting
• Apply activity presets
• Configure multiple devices at once""",
            title="Activate Scene",
            annotations={"title": "Activate Scene"}
        )(self.activate_scene)
        
        self.mcp.tool(
            name="run_script",
            description="""Execute a Home Assistant script.

## Parameters
• script_id: Script entity ID, format: script.name (required)
  - Call `get_ha_scripts` to see available scripts
• data: Variables to pass to script (optional dict)

## Returns
Success status

## Use Cases
• Run complex automation sequences
• Execute conditional logic
• Trigger custom actions""",
            title="Run Script",
            annotations={"title": "Run Script"}
        )(self.run_script_for_mcp)
        
        self.mcp.tool(
            name="trigger_automation",
            description="""Manually trigger a Home Assistant automation.

## Parameters
• automation_id: Automation entity ID, format: automation.name (required)
  - Call `get_ha_automations` to see available automations

## Returns
Success status

## Use Cases
• Force run automations
• Test automation logic
• Override conditions""",
            title="Trigger Automation",
            annotations={"title": "Trigger Automation"}
        )(self.trigger_automation)
        
        # Media control
        self.mcp.tool(
            name="control_media_player",
            description="""Control media players.

## Parameters
• entity_id: Media player entity (required)
  - Call `get_ha_entities` with domain 'media_player' to see available
• action: play/pause/stop/next/previous/volume_up/volume_down/volume_mute (optional)
• volume_level: 0.0-1.0 (optional)
• seek_position: Position in seconds (optional)

## Returns
Success status with media player state

## Use Cases
• Control playback
• Adjust volume
• Manage entertainment devices""",
            title="Control Media Player",
            annotations={"title": "Control Media Player"}
        )(self.control_media_for_mcp)
        
        # Area control
        self.mcp.tool(
            name="control_area_devices",
            description="""Control all devices of a specific type in an area or room.

## Parameters
• area_name: Room/area name or 'all'/'upstairs'/'downstairs' (required)
  - Call `get_ha_areas` to see available areas
• action: turn_on/turn_off/toggle (required)
• domain: light/switch/all (optional, default: light)
• brightness/color_temp/rgb_color/transition: Light-specific options (optional)

## Returns
Success status with list of controlled entities

## Use Cases
• Turn off all lights in a room
• Control entire floor
• Manage multiple devices at once""",
            title="Control Area Devices",
            annotations={"title": "Control Area Devices"}
        )(self.control_area_for_mcp)
        
        # Notification
        self.mcp.tool(
            name="send_notification",
            description="""Send notifications through Home Assistant.

## Parameters
• message: Notification text (required)
• title: Notification title (optional)
• target: Specific device/service (optional)
  - Defaults to default notify service

## Returns
Success/error status

## Use Cases
• Send alerts to phones
• Display messages on TVs
• Trigger TTS announcements""",
            title="Send Notification",
            annotations={"title": "Send Notification"}
        )(self.send_notification_for_mcp)
        
        # Information tools
        # Commented out - use resources instead for read-only data
        # self.mcp.tool(
        #     name="ha_get_areas",
        #     description="Retrieve all configured areas/rooms in Home Assistant. No parameters required. Returns: Array of area objects with id, name, and entity count. Use to: See available areas for device grouping, understand home layout, or prepare for area-based control.",
        #     annotations={"title": "Get Areas"}
        # )(self.get_areas_for_mcp)
        
        # Generic service call
        self.mcp.tool(
            name="call_home_assistant_service",
            description="""Call any Home Assistant service directly.

## Parameters
• domain: Service domain, e.g., light/switch/script (required)
  - Call `get_ha_services` to see available domains
• service: Service name, e.g., turn_on/turn_off (required)
  - Call `get_ha_service_names` for common service names
• entity_id: Target entity (optional)
• service_data: Additional parameters as dict (optional)

## Returns
Service call result

## Use Cases
• Advanced device control
• Access specialized services
• Custom integrations

⚠️ **Note**: Most flexible but requires HA knowledge""",
            title="Call HA Service",
            annotations={"title": "Call HA Service"}
        )(self.call_service_for_mcp)
        
        # Convert all to tools (Claude cannot use resources, only tools)
        self.mcp.tool(
            name="get_ha_states",
            description=f"""Get current states of Home Assistant entities with optional filtering and pagination (cached for {CacheTTL.HA_STATES} seconds).

## Parameters
• entity_ids: List of specific entity IDs to fetch (optional)
• domain: Filter by domain like 'light', 'switch', 'sensor' (optional)
• area: Filter by area/room name (optional)
• limit: Maximum number of results to return (optional, for pagination)
• offset: Number of results to skip (optional, default: 0, for pagination)

## Returns
• Entity states with current values
• Entity attributes
• Total count of returned entities

## Use Cases
• System overview
• Find entity IDs
• Check device states
• Filter by domain or area
• Paginate through large entity lists

## Caching & Optimization
• Data is cached for {CacheTTL.HA_STATES} seconds for real-time performance
• Very short cache to ensure state freshness
• Use filters to reduce data transfer
• Use limit/offset for pagination when dealing with many entities""",
            title="Get Entity States",
            annotations={"title": "Get Entity States"}
        )(self.get_states_paginated_for_mcp)
        
        # Parameterized queries as tools (Claude can only get static resources)
        self.mcp.tool(
            name="get_states_by_domain",
            description=f"""Get states of all entities in a specific domain (uses cached data, {CacheTTL.HA_STATES} seconds).

## Parameters
• domain: Entity domain, e.g., 'light', 'switch', 'sensor' (required)
  - Call `get_ha_domains` to see available domains
• area: Filter by area/room (optional)
  - Call `get_ha_areas` to see available areas

## Returns
Entity states for the specified domain

## Use Cases
• Get all lights or switches
• Domain-specific overview
• Filter by room

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Ensures consistent real-time performance""",
            title="Get States by Domain",
            annotations={"title": "Get States by Domain"}
        )(self.get_states_by_domain_for_mcp)
        
        self.mcp.tool(
            name="get_states_by_area",
            description=f"""Get states of all entities in a specific area (uses cached data, {CacheTTL.HA_STATES} seconds).

## Parameters
• area: Room/area name (required)
  - Call `get_ha_areas` to see available areas
• domain: Filter by entity domain (optional)
  - Call `get_ha_domains` to see available domains

## Returns
Entity states for the specified area

## Use Cases
• Room overview
• Area device status
• Location-based queries

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Combines cached states with area metadata""",
            title="Get States by Area",
            annotations={"title": "Get States by Area"}
        )(self.get_states_by_area_for_mcp)
        
        self.mcp.tool(
            name="get_ha_areas",
            description=f"""Get all configured areas/rooms in your Home Assistant setup (cached for {CacheTTL.HA_AREAS//3600} hour).

## Parameters
• minimal: Return reduced data (default: True) or full details (False)

## Returns
• Area names and IDs
• Entity counts per area (minimal mode)
• Area hierarchy
• Full mode adds: aliases, labels, icon, picture

## Use Cases
• See home layout
• Get area names for other tools
• Understand room organization

## Caching & Optimization
• Data is cached for {CacheTTL.HA_AREAS//3600} hour as areas rarely change
• Use minimal=True (default) for 70% less data transfer
• Use minimal=False when you need aliases, labels, or pictures""",
            title="Home Areas",
            annotations={"title": "Home Areas"}
        )(self.get_areas_resource)
        
        self.mcp.tool(
            name="get_ha_devices",
            description=f"""Get all devices registered in Home Assistant (cached for {CacheTTL.HA_DEVICE_LIST//60} minutes).

## Parameters
• minimal: Return reduced data (default: True) or full details (False)
• limit: Maximum number of results to return (optional, for pagination)
• offset: Number of results to skip (optional, default: 0, for pagination)

## Returns
• Device names and IDs
• Device manufacturers and models
• Associated entities
• Area assignments
• Full mode adds: sw_version, hw_version, configuration_url, connections, identifiers

## Use Cases
• Device inventory
• Find device entities
• Hardware overview
• Paginate through large device lists

## Caching & Optimization
• Device list cached for {CacheTTL.HA_DEVICE_LIST//60} minutes (metadata rarely changes)
• Use minimal=True (default) for 50% less data transfer
• Use minimal=False for version info and technical details
• Use limit/offset for pagination when dealing with many devices""",
            title="All Devices",
            annotations={"title": "All Devices"}
        )(self.get_devices_paginated_for_mcp)
        
        self.mcp.tool(
            name="get_ha_entities",
            description=f"""Get all entities configured in Home Assistant (cached for {CacheTTL.HA_ENTITY_LIST//60} minutes).

## Parameters
• minimal: Return reduced data (default: True) or full details (False)
• limit: Maximum number of results to return (optional, for pagination)
• offset: Number of results to skip (optional, default: 0, for pagination)

## Returns
• Entity IDs and names
• Entity domains
• Friendly names
• Device associations
• Full mode adds: icon, unit_of_measurement, hidden, disabled, entity_category

## Use Cases
• Find entity IDs for control
• System inventory
• Entity discovery
• Paginate through large entity lists

## Caching & Optimization
• Entity list cached for {CacheTTL.HA_ENTITY_LIST//60} minutes (metadata rarely changes)
• Use minimal=True (default) for 40% less data transfer
• Use minimal=False for icons, units, and entity categories
• Use limit/offset for pagination when dealing with many entities""",
            title="All Entities",
            annotations={"title": "All Entities"}
        )(self.get_entities_paginated_for_mcp)
        
        self.mcp.tool(
            name="get_ha_services",
            description=f"""Get all available services that can be called in Home Assistant (cached for {CacheTTL.HA_SERVICES//3600} hour).

## Returns
• Service domains
• Service names per domain
• Service descriptions
• Required parameters

## Use Cases
• Discover available services
• Reference for `call_home_assistant_service`
• Service documentation

## Caching
• Service list cached for {CacheTTL.HA_SERVICES//3600} hour as services rarely change
• Reduces API calls for service discovery""",
            title="Available Services",
            annotations={"title": "Available Services"}
        )(self.get_services_resource)
        
        # Keep these as tools since they have required parameters
        self.mcp.tool(
            name="get_entity_state",
            description="""Get detailed state information for a specific entity.

## Parameters
• entity_id: Entity identifier (required)
  - Call `get_ha_entities` to find entity IDs

## Returns
• Current state
• All attributes
• Last changed time
• Device information

## Use Cases
• Detailed entity inspection
• Get specific attributes
• Debug entity issues""",
            title="Get Entity State",
            annotations={"title": "Get Entity State"}
        )(self.get_entity_state_resource)
        
        self.mcp.tool(
            name="get_entity_history",
            description="""Get recent history for a specific entity.

## Parameters
• entity_id: Entity identifier (required)
  - Call `get_ha_entities` to find entity IDs
• hours: Hours of history to retrieve (optional, default 24)

## Returns
• Historical state changes
• Timestamps
• State transitions

## Use Cases
• Track entity changes
• Analyze patterns
• Debug automations""",
            title="Get Entity History",
            annotations={"title": "Get Entity History"}
        )(self.get_entity_history_resource)
        
        self.mcp.tool(
            name="get_sensors_by_type",
            description="""Get all sensors of a specific type.

## Parameters
• sensor_type: Type of sensor (required)
  - Options: 'temperature', 'humidity', 'motion', 'door', 'window', 'battery'
  - Call `get_ha_device_classes` for all sensor types

## Returns
All sensors of the specified type with current values

## Use Cases
• Get all temperature readings
• Check motion sensors
• Monitor door/window status""",
            title="Get Sensors by Type",
            annotations={"title": "Get Sensors by Type"}
        )(self.get_sensors_by_type_resource)
        
        self.mcp.tool(
            name="get_ha_scenes",
            description="""Get all available scenes that can be activated.

## Returns
• Scene names and IDs
• Scene entity IDs
• Associated areas

## Use Cases
• See available scenes
• Get scene IDs for activation
• Scene management""",
            title="Available Scenes",
            annotations={"title": "Available Scenes"}
        )(self.get_scenes_resource)
        
        self.mcp.tool(
            name="get_ha_automations",
            description="""Get all configured automations with their status.

## Returns
• Automation names and IDs
• Enabled/disabled status
• Last triggered time
• Trigger counts

## Use Cases
• Automation overview
• Check automation status
• Debug automation issues""",
            title="Automations",
            annotations={"title": "Automations"}
        )(self.get_automations_resource)
        
        self.mcp.tool(
            name="get_ha_scripts",
            description="""Get all configured scripts that can be executed.

## Returns
• Script names and IDs
• Script entity IDs
• Last run time

## Use Cases
• See available scripts
• Get script IDs for execution
• Script management""",
            title="Available Scripts",
            annotations={"title": "Available Scripts"}
        )(self.get_scripts_resource)
        
        self.mcp.tool(
            name="get_ha_unavailable_entities",
            description="""Get all entities that are currently unavailable or offline.

## Returns
• Unavailable entity list
• Entity domains
• Last seen times
• Device associations

## Use Cases
• Find offline devices
• Troubleshoot connectivity
• System health check""",
            title="Unavailable Entities",
            annotations={"title": "Unavailable Entities"}
        )(self.get_unavailable_entities_resource)
        
        # Tools for constant values (for LLM discovery)
        self.mcp.tool(
            name="get_ha_domains",
            description="""Get all available entity domains in Home Assistant.

## Returns
• List of domains (light, switch, sensor, etc.)
• Entity count per domain
• Domain descriptions

## Use Cases
• Understand system capabilities
• Reference for domain filtering
• System overview""",
            title="Entity Domains",
            annotations={"title": "Entity Domains"}
        )(self.get_domains_resource)
        
        self.mcp.tool(
            name="get_ha_device_classes",
            description="""Get available device classes for different entity types.

## Returns
• Device classes per domain
• Class descriptions
• Common examples

## Use Cases
• Understand sensor types
• Reference for filtering
• Device categorization""",
            title="Device Classes",
            annotations={"title": "Device Classes"}
        )(self.get_device_classes_resource)
        
        self.mcp.tool(
            name="get_ha_service_names",
            description="""Get commonly used service names for each domain.

## Returns
• Common services per domain
• Service descriptions
• Parameter requirements

## Use Cases
• Quick service reference
• Learn service names
• Reference for `call_home_assistant_service`""",
            title="Common Service Names",
            annotations={"title": "Common Service Names"}
        )(self.get_service_names_resource)
        
        # Additional tools for common queries
        self.mcp.tool(
            name="get_ha_lights_on",
            description=f"""Get all lights that are currently turned on (uses cached data, {CacheTTL.HA_STATES} seconds).

## Returns
• List of on lights
• Brightness levels
• Color settings
• Area locations

## Use Cases
• Check what lights are on
• Energy monitoring
• Before leaving home

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Filters for lights in 'on' state""",
            title="Lights Currently On",
            annotations={"title": "Lights Currently On"}
        )(self.get_lights_on_resource)
        
        self.mcp.tool(
            name="get_ha_devices_on",
            description=f"""Get all devices that are currently on or active (uses cached data, {CacheTTL.HA_STATES} seconds).

## Returns
• All active devices
• Device types
• Power consumption (if available)
• Area locations

## Use Cases
• Energy monitoring
• Security check
• Bedtime routines

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Filters for active/on devices across all domains""",
            title="Devices Currently On",
            annotations={"title": "Devices Currently On"}
        )(self.get_devices_on_resource)
        
        self.mcp.tool(
            name="get_ha_temperature_sensors",
            description=f"""Get all temperature sensors with current readings (uses cached data, {CacheTTL.HA_STATES} seconds).

## Returns
• Temperature sensor list
• Current temperatures
• Units (°C/°F)
• Area locations

## Use Cases
• Climate monitoring
• Room temperatures
• HVAC optimization

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Filters for temperature device class sensors""",
            title="Temperature Sensors",
            annotations={"title": "Temperature Sensors"}
        )(self.get_temperature_sensors_resource)
        
        self.mcp.tool(
            name="get_ha_motion_sensors",
            description=f"""Get all motion sensors and their current state (uses cached data, {CacheTTL.HA_STATES} seconds).

## Returns
• Motion sensor list
• Detection status
• Last motion time
• Area locations

## Use Cases
• Security monitoring
• Occupancy detection
• Automation triggers

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Filters for motion and occupancy sensors""",
            title="Motion Sensors",
            annotations={"title": "Motion Sensors"}
        )(self.get_motion_sensors_resource)
        
        self.mcp.tool(
            name="get_ha_door_window_sensors",
            description=f"""Get all door and window sensors with open/closed status (uses cached data, {CacheTTL.HA_STATES} seconds).

## Returns
• Door/window sensor list
• Open/closed status
• Last changed time
• Area locations

## Use Cases
• Security check
• Climate control
• Before leaving home

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Filters for door, window, and opening sensors""",
            title="Door & Window Sensors",
            annotations={"title": "Door & Window Sensors"}
        )(self.get_door_window_sensors_resource)
        
        self.mcp.tool(
            name="get_ha_security_status",
            description=f"""Get security-related information (locks, alarms, cameras) (uses cached data, {CacheTTL.HA_STATES} seconds).

## Returns
• Lock status
• Alarm state
• Camera status
• Security sensor states

## Use Cases
• Security overview
• Bedtime check
• Vacation monitoring

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Aggregates security-related entities""",
            title="Security Status",
            annotations={"title": "Security Status"}
        )(self.get_security_status_resource)
        
        self.mcp.tool(
            name="get_ha_climate_status",
            description=f"""Get current climate control status (thermostats, humidity, air quality) (uses cached data, {CacheTTL.HA_STATES} seconds).

## Returns
• Thermostat settings
• Current temperatures
• Humidity levels
• Air quality metrics

## Use Cases
• Climate overview
• Comfort monitoring
• Energy optimization

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Aggregates climate and air quality entities""",
            title="Climate Status",
            annotations={"title": "Climate Status"}
        )(self.get_climate_status_resource)
        
        self.mcp.tool(
            name="get_ha_battery_status",
            description=f"""Get battery levels for all battery-powered devices (uses cached data, {CacheTTL.HA_STATES} seconds).

## Returns
• Battery levels
• Low battery warnings
• Device names
• Last updated times

## Use Cases
• Maintenance alerts
• Battery replacement planning
• Device health monitoring

## Caching
• Uses cached state data ({CacheTTL.HA_STATES} seconds refresh)
• Filters for battery level sensors""",
            title="Battery Status",
            annotations={"title": "Battery Status"}
        )(self.get_battery_status_resource)
        
        # Sensor Categorization Tools
        self.mcp.tool(
            name="categorize_sensors",
            description="""Categorize all sensors by type (weather, pool, air quality, HVAC, indoor temp, etc.).

## Returns
• Categorized sensors by type
• Summary statistics
• Recommendations

## Categories
• weather: Outdoor weather sensors (temp, humidity, wind, rain, UV, etc.)
• pool: Pool/spa sensors (water temp, pH, chlorine, pump status)
• indoor_air_quality: IAQ sensors (CO2, VOC, PM2.5, radon, smoke)
• hvac: HVAC sensors (thermostat, setpoints, zone temps)
• indoor_temperature: Room temperature sensors (non-HVAC)
• outdoor: Other outdoor sensors
• energy: Power/energy consumption sensors
• security: Motion, door, window sensors
• other: Uncategorized sensors

## Use Cases
• Organize large sensor collections
• Identify sensor gaps
• Separate environmental monitoring""",
            title="Categorize Sensors",
            annotations={"title": "Categorize Sensors"}
        )(self.categorize_sensors_for_mcp)
        
        self.mcp.tool(
            name="get_weather_sensors",
            description="""Get all weather-related sensors.

## Returns
• Weather station sensors
• Outdoor temperature/humidity
• Wind, rain, UV sensors
• Barometric pressure
• Weather forecast entities

## Use Cases
• Weather monitoring dashboard
• Outdoor automation triggers
• Climate analysis""",
            title="Weather Sensors",
            annotations={"title": "Weather Sensors"}
        )(self.get_weather_sensors_for_mcp)
        
        self.mcp.tool(
            name="get_pool_sensors",
            description="""Get all pool and spa related sensors.

## Returns
• Water temperature
• pH levels
• Chlorine/chemical sensors
• Pump/filter status
• Heater status

## Use Cases
• Pool maintenance monitoring
• Chemical balance alerts
• Equipment status tracking""",
            title="Pool Sensors",
            annotations={"title": "Pool Sensors"}
        )(self.get_pool_sensors_for_mcp)
        
        self.mcp.tool(
            name="get_air_quality_sensors",
            description="""Get all indoor air quality sensors.

## Returns
• CO2 levels
• VOC (Volatile Organic Compounds)
• PM2.5/PM10 particulates
• Carbon monoxide
• Radon levels
• Indoor humidity

## Use Cases
• Health monitoring
• Ventilation control
• Air purifier automation""",
            title="Air Quality Sensors",
            annotations={"title": "Air Quality Sensors"}
        )(self.get_air_quality_sensors_for_mcp)
        
        self.mcp.tool(
            name="get_hvac_sensors",
            description="""Get all HVAC-related sensors.

## Returns
• Thermostat readings
• Setpoints/targets
• Zone temperatures
• System status
• Fan/damper positions

## Use Cases
• Climate control monitoring
• Energy efficiency analysis
• Multi-zone coordination""",
            title="HVAC Sensors",
            annotations={"title": "HVAC Sensors"}
        )(self.get_hvac_sensors_for_mcp)
        
        self.mcp.tool(
            name="get_indoor_temp_sensors",
            description="""Get all indoor temperature sensors (non-HVAC).

## Returns
• Room temperature sensors
• Area-specific temperatures
• Non-thermostat readings

## Use Cases
• Room comfort monitoring
• Temperature differential analysis
• Occupancy-based control""",
            title="Indoor Temperature Sensors",
            annotations={"title": "Indoor Temperature Sensors"}
        )(self.get_indoor_temp_sensors_for_mcp)
    
    # MCP Tool Wrappers
    def turn_on_for_mcp(self, entity_id: str) -> Dict[str, Any]:
        """MCP wrapper for turn_on - simple on to default/full state"""
        try:
            # Simple turn on - device will use its default "on" state
            # For lights, this is typically full brightness
            return self.turn_on(entity_id)
            
        except Exception as e:
            return {
                "error": str(e),
                "entity_id": entity_id,
                "help": "Check entity_id is valid using get_ha_entities or get_ha_states"
            }
    
    def turn_off_for_mcp(self, entity_id: str) -> Dict[str, Any]:
        """MCP wrapper for turn_off - simple off"""
        try:
            # Simple turn off - device will turn completely off
            return self.turn_off(entity_id)
            
        except Exception as e:
            return {
                "error": str(e),
                "entity_id": entity_id,
                "help": "Check entity_id is valid using get_ha_entities or get_ha_states"
            }
    
    def toggle_for_mcp(self, entity_id: str) -> Dict[str, Any]:
        """MCP wrapper for toggle"""
        return self.toggle(entity_id)
    
    def set_light_level_for_mcp(self, entity_id: str, brightness_percent: str) -> Dict[str, Any]:
        """MCP wrapper for setting light brightness level with percentage input"""
        try:
            # Convert percentage string to 0-255 brightness value
            try:
                percent = float(brightness_percent)
                if percent < 0 or percent > 100:
                    return {
                        "error": f"Invalid brightness percentage: {brightness_percent}",
                        "help": "Brightness must be between 0-100%",
                        "examples": ["0 (off)", "25 (dim)", "50 (half)", "75 (bright)", "100 (full)"]
                    }
                
                # Convert percentage to 0-255 scale (with proper rounding)
                brightness = round((percent / 100) * 255)
                
            except (ValueError, TypeError):
                return {
                    "error": f"Invalid brightness percentage: {brightness_percent}",
                    "help": "Brightness must be a number between 0-100",
                    "examples": ["0", "25", "50", "75", "100"]
                }
            
            # Call turn_on with the calculated brightness
            return self.turn_on(entity_id, brightness=brightness)
            
        except Exception as e:
            return {
                "error": str(e),
                "entity_id": entity_id,
                "help": "Check that entity_id is a dimmable light using get_ha_lights"
            }
    
    def set_climate_for_mcp(self, entity_id: str, temperature: Optional[Union[float, str]] = None,
                           hvac_mode: Optional[str] = None, preset_mode: Optional[str] = None) -> Dict[str, Any]:
        """MCP wrapper for climate control with type conversion"""
        try:
            # Convert temperature string to float if needed
            if temperature is not None and temperature != '':
                try:
                    temperature = float(temperature) if isinstance(temperature, str) else temperature
                except (ValueError, TypeError):
                    return {
                        "error": f"Invalid temperature value: {temperature}",
                        "help": "Temperature must be a number"
                    }
            else:
                temperature = None
                
            if temperature is not None:
                return self.call_service('climate', 'set_temperature', entity_id, temperature=temperature, hvac_mode=hvac_mode)
            elif hvac_mode is not None:
                return self.call_service('climate', 'set_hvac_mode', entity_id, hvac_mode=hvac_mode)
            elif preset_mode is not None:
                return self.call_service('climate', 'set_preset_mode', entity_id, preset_mode=preset_mode)
            else:
                return {"error": "Must specify temperature, hvac_mode, or preset_mode"}
        except Exception as e:
            return {"error": str(e), "entity_id": entity_id}
    
    def control_cover_for_mcp(self, entity_id: str, action: str, position: Optional[Union[int, str]] = None) -> Dict[str, Any]:
        """MCP wrapper for cover control with type conversion"""
        try:
            # Convert position string to int if needed
            if position is not None and position != '':
                try:
                    position = int(position) if isinstance(position, str) else position
                    if position < 0 or position > 100:
                        return {
                            "error": f"Invalid position value: {position}",
                            "help": "Position must be between 0-100"
                        }
                except (ValueError, TypeError):
                    return {
                        "error": f"Invalid position value: {position}",
                        "help": "Position must be a number between 0-100"
                    }
            
            if action == "open":
                return self.call_service('cover', 'open_cover', entity_id)
            elif action == "close":
                return self.call_service('cover', 'close_cover', entity_id)
            elif action == "stop":
                return self.call_service('cover', 'stop_cover', entity_id)
            elif action == "set_position" and position is not None:
                return self.call_service('cover', 'set_cover_position', entity_id, position=position)
            else:
                return {"error": f"Invalid action '{action}' or missing position for set_position"}
        except Exception as e:
            return {"error": str(e), "entity_id": entity_id}
    
    def lock_control_for_mcp(self, entity_id: str, action: str, code: Optional[str] = None) -> Dict[str, Any]:
        """MCP wrapper for lock control"""
        if action == "lock":
            return self.call_service('lock', 'lock', entity_id)
        elif action == "unlock":
            kwargs = {}
            if code:
                kwargs['code'] = code
            return self.call_service('lock', 'unlock', entity_id, **kwargs)
        else:
            return {"error": f"Invalid action '{action}'. Use 'lock' or 'unlock'"}
    
    def run_script_for_mcp(self, script_id: str, variables: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """MCP wrapper for run_script"""
        return self.run_script(script_id, **(variables or {}))
    
    def control_media_for_mcp(self, entity_id: str, action: str, volume: Optional[Union[float, str]] = None) -> Dict[str, Any]:
        """MCP wrapper for media control with type conversion"""
        try:
            # Convert volume string to float if needed
            if volume is not None and volume != '':
                try:
                    volume = float(volume) if isinstance(volume, str) else volume
                    if volume < 0.0 or volume > 1.0:
                        return {
                            "error": f"Invalid volume value: {volume}",
                            "help": "Volume must be between 0.0 and 1.0"
                        }
                except (ValueError, TypeError):
                    return {
                        "error": f"Invalid volume value: {volume}",
                        "help": "Volume must be a number between 0.0 and 1.0"
                    }
            
            if action == "play":
                return self.call_service('media_player', 'media_play', entity_id)
            elif action == "pause":
                return self.call_service('media_player', 'media_pause', entity_id)
            elif action == "stop":
                return self.call_service('media_player', 'media_stop', entity_id)
            elif action == "next":
                return self.call_service('media_player', 'media_next_track', entity_id)
            elif action == "previous":
                return self.call_service('media_player', 'media_previous_track', entity_id)
            elif action == "volume_set" and volume is not None:
                return self.call_service('media_player', 'volume_set', entity_id, volume_level=volume)
            elif action == "volume_mute":
                return self.call_service('media_player', 'volume_mute', entity_id, is_volume_muted=True)
            elif action == "volume_unmute":
                return self.call_service('media_player', 'volume_mute', entity_id, is_volume_muted=False)
            else:
                return {"error": f"Invalid action '{action}' or missing volume for volume_set"}
        except Exception as e:
            return {"error": str(e), "entity_id": entity_id}
    
    def control_area_for_mcp(self, area_name: str, action: str, domain: str = "light",
                            brightness: Optional[int] = None, color_temp: Optional[int] = None,
                            rgb_color: Optional[List[int]] = None, transition: Optional[int] = None) -> Dict[str, Any]:
        """MCP wrapper for area control"""
        # Get entities in area
        states = self.get_states(area=area_name, domain=domain if domain != "all" else None)
        entity_ids = [s['entity_id'] for s in states.get('states', [])]
        
        if not entity_ids:
            return {"error": f"No {domain} entities found in area {area_name}"}
        
        results = []
        kwargs = {}
        if brightness is not None:
            kwargs['brightness'] = brightness
        if color_temp is not None:
            kwargs['color_temp'] = color_temp
        if rgb_color is not None:
            kwargs['rgb_color'] = rgb_color
        if transition is not None:
            kwargs['transition'] = transition
        
        for entity_id in entity_ids:
            if action == "turn_on":
                r = self.call_service(entity_id.split('.')[0], 'turn_on', entity_id, **kwargs)
            elif action == "turn_off":
                r = self.call_service(entity_id.split('.')[0], 'turn_off', entity_id, **kwargs)
            elif action == "toggle":
                r = self.call_service(entity_id.split('.')[0], 'toggle', entity_id, **kwargs)
            results.append(r)
        
        return {"status": "success", "area": area_name, "entities_controlled": entity_ids, "results": results}
    
    def send_notification_for_mcp(self, message: str, title: Optional[str] = None, target: Optional[str] = None) -> Dict[str, Any]:
        """MCP wrapper for send_notification"""
        service_name = target or 'notify'
        return self.send_notification(message, title, service_name)
    
    def get_areas_for_mcp(self) -> Dict[str, Any]:
        """MCP wrapper for get_areas"""
        areas = self.get_areas()
        return {"areas": areas, "count": len(areas)}
    
    def call_service_for_mcp(self, domain: str, service: str, entity_id: Optional[str] = None,
                            service_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """MCP wrapper for call_service that accepts a dict instead of **kwargs"""
        if service_data:
            return self.call_service(domain, service, entity_id, **service_data)
        else:
            return self.call_service(domain, service, entity_id)
    
    # Tool wrapper methods for parameterized queries
    def get_states_by_domain_for_mcp(self, domain: str, area: Optional[str] = None) -> Dict[str, Any]:
        """Get states filtered by domain and optionally by area"""
        return self.get_states(domain=domain, area=area)
    
    def get_states_by_area_for_mcp(self, area: str, domain: Optional[str] = None) -> Dict[str, Any]:
        """Get states filtered by area and optionally by domain"""
        return self.get_states(area=area, domain=domain)
    
    # MCP Tool Wrapper Methods for Pagination
    def get_states_paginated_for_mcp(self, limit: Optional[Union[int, str]] = None, 
                                     offset: Optional[Union[int, str]] = None,
                                     entity_ids: Optional[List[str]] = None,
                                     domain: Optional[str] = None, 
                                     area: Optional[str] = None) -> Dict[str, Any]:
        """MCP wrapper for paginated get_states with type conversion"""
        try:
            # Convert string parameters to integers if needed
            if limit is not None and limit != '':
                limit = int(limit) if isinstance(limit, str) else limit
            else:
                limit = None
                
            if offset is not None and offset != '':
                offset = int(offset) if isinstance(offset, str) else offset
            else:
                offset = 0  # Default to 0 if None or empty string
            
            return self.get_states(entity_ids=entity_ids, domain=domain, area=area, 
                                  limit=limit, offset=offset)
        except ValueError as e:
            return {"error": f"Invalid pagination parameters: {e}", "states": [], "count": 0}
        except Exception as e:
            return {"error": str(e), "states": [], "count": 0}
    
    def get_devices_paginated_for_mcp(self, minimal: Optional[Union[bool, str]] = None, 
                                      limit: Optional[Union[int, str]] = None, 
                                      offset: Optional[Union[int, str]] = None) -> List[Dict[str, Any]]:
        """MCP wrapper for paginated get_devices with type conversion"""
        try:
            # Convert string parameters to appropriate types
            if minimal is not None and isinstance(minimal, str):
                minimal = minimal.lower() in ['true', '1', 'yes']
            elif minimal is None:
                minimal = True  # Default value
                
            if limit is not None and limit != '':
                limit = int(limit) if isinstance(limit, str) else limit
            else:
                limit = None
                
            if offset is not None and offset != '':
                offset = int(offset) if isinstance(offset, str) else offset
            else:
                offset = 0  # Default to 0 if None or empty string
            
            return self.get_devices(minimal=minimal, limit=limit, offset=offset)
        except ValueError as e:
            logger.error(f"Invalid parameters for get_devices: {e}")
            return []
        except Exception as e:
            logger.error(f"Error in get_devices: {e}")
            return []
    
    def get_entities_paginated_for_mcp(self, minimal: Optional[Union[bool, str]] = None,
                                       limit: Optional[Union[int, str]] = None, 
                                       offset: Optional[Union[int, str]] = None) -> List[Dict[str, Any]]:
        """MCP wrapper for paginated get_entities with type conversion"""
        try:
            # Convert string parameters to appropriate types
            if minimal is not None and isinstance(minimal, str):
                minimal = minimal.lower() in ['true', '1', 'yes']
            elif minimal is None:
                minimal = True  # Default value
                
            if limit is not None and limit != '':
                limit = int(limit) if isinstance(limit, str) else limit
            else:
                limit = None
                
            if offset is not None and offset != '':
                offset = int(offset) if isinstance(offset, str) else offset
            else:
                offset = 0  # Default to 0 if None or empty string
            
            return self.get_entities(minimal=minimal, limit=limit, offset=offset)
        except ValueError as e:
            logger.error(f"Invalid parameters for get_entities: {e}")
            return []
        except Exception as e:
            logger.error(f"Error in get_entities: {e}")
            return []
    
    # Resource Methods
    def get_all_states_resource(self) -> Dict[str, Any]:
        """Resource providing all entity states"""
        result = self.get_states()
        states = result.get('states', [])
        return {
            "entities": states,
            "entity_count": len(states),
            "by_domain": self._group_by_domain(states)
        }
    
    def get_areas_resource(self) -> List[Dict[str, Any]]:
        """Resource providing all areas"""
        return self.get_areas()
    
    def get_devices_resource(self) -> List[Dict[str, Any]]:
        """Resource providing all devices"""
        return self.get_devices()
    
    def get_entities_resource(self) -> List[Dict[str, Any]]:
        """Resource providing all entities"""
        return self.get_entities()
    
    def get_services_resource(self) -> Dict[str, Any]:
        """Resource providing all available services"""
        return self.get_services()
    
    
    def get_entity_state_resource(self, entity_id: str) -> Dict[str, Any]:
        """Resource providing detailed state for a single entity"""
        try:
            result = self.get_states(entity_id=entity_id)
            states = result.get('states', [])
            if states:
                return states[0]
            else:
                return {"error": "Entity not found", "entity_id": entity_id}
        except Exception as e:
            logger.error(f"Error getting state for entity {entity_id}: {e}")
            return {"error": str(e), "entity_id": entity_id}
    
    def get_entity_history_resource(self, entity_id: str, hours: Union[int, str] = 24) -> Dict[str, Any]:
        """Resource providing history for a specific entity with type conversion"""
        try:
            # Convert hours string to int if needed
            if isinstance(hours, str):
                try:
                    hours = int(hours)
                    if hours <= 0:
                        hours = 24  # Default to 24 hours if invalid
                except (ValueError, TypeError):
                    hours = 24  # Default to 24 hours if can't convert
            
            # Get history for the specified period
            from datetime import datetime, timedelta, timezone
            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(hours=hours)
            
            history = self.get_history(entity_id, start_time, end_time)
            
            return {
                "entity_id": entity_id,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "history": history,
                "state_changes": len(history)
            }
        except Exception as e:
            logger.error(f"Error getting history for entity {entity_id}: {e}")
            return {"error": str(e), "entity_id": entity_id}
    
    def get_scenes_resource(self) -> Dict[str, Any]:
        """Resource providing all available scenes"""
        try:
            result = self.get_states(domain="scene")
            scenes = result.get('states', [])
            return {
                "scenes": scenes,
                "scene_count": len(scenes)
            }
        except Exception as e:
            logger.error(f"Error getting scenes: {e}")
            return {"error": str(e)}
    
    def get_automations_resource(self) -> Dict[str, Any]:
        """Resource providing all automations"""
        try:
            result = self.get_states(domain="automation")
            automations = result.get('states', [])
            return {
                "automations": automations,
                "automation_count": len(automations),
                "enabled_count": sum(1 for a in automations if a.get('state') == 'on')
            }
        except Exception as e:
            logger.error(f"Error getting automations: {e}")
            return {"error": str(e)}
    
    def get_scripts_resource(self) -> Dict[str, Any]:
        """Resource providing all scripts"""
        try:
            result = self.get_states(domain="script")
            scripts = result.get('states', [])
            return {
                "scripts": scripts,
                "script_count": len(scripts)
            }
        except Exception as e:
            logger.error(f"Error getting scripts: {e}")
            return {"error": str(e)}
    
    def get_sensors_by_type_resource(self, sensor_type: str) -> Dict[str, Any]:
        """Resource providing sensors of a specific type"""
        try:
            result = self.get_states(domain="sensor")
            sensors = result.get('states', [])
            
            # Filter sensors by type based on attributes or entity_id patterns
            filtered_sensors = []
            for sensor in sensors:
                entity_id = sensor.get('entity_id', '')
                attributes = sensor.get('attributes', {})
                device_class = attributes.get('device_class', '')
                
                # Check if sensor matches the requested type
                if sensor_type.lower() in entity_id.lower() or sensor_type.lower() == device_class.lower():
                    filtered_sensors.append(sensor)
            
            return {
                "sensor_type": sensor_type,
                "sensors": filtered_sensors,
                "sensor_count": len(filtered_sensors)
            }
        except Exception as e:
            logger.error(f"Error getting sensors of type {sensor_type}: {e}")
            return {"error": str(e), "sensor_type": sensor_type}
    
    def get_unavailable_entities_resource(self) -> Dict[str, Any]:
        """Resource providing unavailable entities"""
        try:
            result = self.get_states()
            all_states = result.get('states', [])
            unavailable = [s for s in all_states if s.get('state') in ['unavailable', 'unknown']]
            
            return {
                "unavailable_entities": unavailable,
                "unavailable_count": len(unavailable),
                "total_entities": len(all_states)
            }
        except Exception as e:
            logger.error(f"Error getting unavailable entities: {e}")
            return {"error": str(e)}
    
    def categorize_sensors(self) -> Dict[str, Any]:
        """Categorize all sensors by type (weather, pool, air quality, HVAC, etc.)"""
        try:
            from helpers.sensor_categorizer import SensorCategorizer, SensorCategory
            
            # Get all entities
            result = self.get_states()
            all_entities = result.get('states', [])
            
            # Initialize categorizer
            categorizer = SensorCategorizer()
            
            # Categorize sensors
            categorized = categorizer.categorize_sensors(all_entities)
            
            # Get summary
            summary = categorizer.get_category_summary(categorized)
            
            # Get recommendations
            recommendations = categorizer.get_recommendations(categorized)
            
            return {
                "categorized": categorized,
                "summary": summary,
                "recommendations": recommendations
            }
        except Exception as e:
            logger.error(f"Error categorizing sensors: {e}")
            return {"error": str(e)}
    
    def get_sensors_by_category(self, category: str) -> Dict[str, Any]:
        """Get sensors for a specific category"""
        try:
            from helpers.sensor_categorizer import SensorCategorizer, SensorCategory
            
            # Validate category
            try:
                category_enum = SensorCategory[category.upper()]
            except KeyError:
                return {
                    "error": f"Invalid category: {category}",
                    "valid_categories": [c.value for c in SensorCategory]
                }
            
            # Get all entities
            result = self.get_states()
            all_entities = result.get('states', [])
            
            # Initialize categorizer and filter
            categorizer = SensorCategorizer()
            filtered = categorizer.filter_by_categories(all_entities, [category])
            
            # Get details for each sensor
            detailed = [categorizer.get_sensor_details(e) for e in filtered]
            
            return {
                "category": category,
                "sensors": detailed,
                "count": len(detailed)
            }
        except Exception as e:
            logger.error(f"Error getting sensors by category: {e}")
            return {"error": str(e)}
    
    def categorize_sensors_for_mcp(self) -> Dict[str, Any]:
        """MCP wrapper for categorize_sensors"""
        return self.categorize_sensors()
    
    def get_weather_sensors_for_mcp(self) -> Dict[str, Any]:
        """MCP tool to get all weather-related sensors"""
        return self.get_sensors_by_category("weather")
    
    def get_pool_sensors_for_mcp(self) -> Dict[str, Any]:
        """MCP tool to get all pool-related sensors"""
        return self.get_sensors_by_category("pool")
    
    def get_air_quality_sensors_for_mcp(self) -> Dict[str, Any]:
        """MCP tool to get all indoor air quality sensors"""
        return self.get_sensors_by_category("indoor_air_quality")
    
    def get_hvac_sensors_for_mcp(self) -> Dict[str, Any]:
        """MCP tool to get all HVAC-related sensors"""
        return self.get_sensors_by_category("hvac")
    
    def get_indoor_temp_sensors_for_mcp(self) -> Dict[str, Any]:
        """MCP tool to get all indoor temperature sensors (non-HVAC)"""
        return self.get_sensors_by_category("indoor_temperature")
    
    def get_domains_resource(self) -> Dict[str, Any]:
        """Resource providing available Home Assistant domains"""
        return {
            "domains": [
                {"domain": "light", "description": "Lighting control", "common_attributes": ["brightness", "color_temp", "rgb_color"]},
                {"domain": "switch", "description": "On/off switches", "common_attributes": ["power", "current"]},
                {"domain": "sensor", "description": "Sensor readings", "common_attributes": ["unit_of_measurement", "device_class"]},
                {"domain": "binary_sensor", "description": "Binary sensors (on/off)", "common_attributes": ["device_class"]},
                {"domain": "climate", "description": "Thermostats and HVAC", "common_attributes": ["temperature", "target_temp_high", "target_temp_low"]},
                {"domain": "cover", "description": "Covers, blinds, garage doors", "common_attributes": ["position", "tilt_position"]},
                {"domain": "lock", "description": "Smart locks", "common_attributes": ["locked"]},
                {"domain": "media_player", "description": "Media devices", "common_attributes": ["volume_level", "source", "media_title"]},
                {"domain": "fan", "description": "Fans", "common_attributes": ["speed", "oscillating", "direction"]},
                {"domain": "vacuum", "description": "Robot vacuums", "common_attributes": ["battery_level", "status"]},
                {"domain": "camera", "description": "Cameras", "common_attributes": ["video_url", "still_image_url"]},
                {"domain": "scene", "description": "Scenes", "common_attributes": []},
                {"domain": "automation", "description": "Automations", "common_attributes": ["last_triggered"]},
                {"domain": "script", "description": "Scripts", "common_attributes": ["last_triggered"]},
                {"domain": "input_boolean", "description": "Boolean helpers", "common_attributes": []},
                {"domain": "input_number", "description": "Number helpers", "common_attributes": ["min", "max", "step"]},
                {"domain": "input_text", "description": "Text helpers", "common_attributes": ["min", "max", "pattern"]},
                {"domain": "input_select", "description": "Dropdown helpers", "common_attributes": ["options"]},
                {"domain": "timer", "description": "Timer helpers", "common_attributes": ["duration", "remaining"]},
                {"domain": "counter", "description": "Counter helpers", "common_attributes": ["minimum", "maximum", "step"]}
            ],
            "usage": "Use domain name in ha://states/domain/{domain} or when filtering entities"
        }
    
    def get_device_classes_resource(self) -> Dict[str, Any]:
        """Resource providing device classes for different entity types"""
        return {
            "sensor_classes": [
                {"class": "temperature", "unit": "°C or °F", "description": "Temperature measurement"},
                {"class": "humidity", "unit": "%", "description": "Humidity level"},
                {"class": "pressure", "unit": "hPa, mbar", "description": "Atmospheric pressure"},
                {"class": "illuminance", "unit": "lx", "description": "Light level"},
                {"class": "battery", "unit": "%", "description": "Battery level"},
                {"class": "power", "unit": "W, kW", "description": "Power consumption"},
                {"class": "energy", "unit": "kWh", "description": "Energy usage"},
                {"class": "current", "unit": "A", "description": "Electrical current"},
                {"class": "voltage", "unit": "V", "description": "Electrical voltage"},
                {"class": "co2", "unit": "ppm", "description": "CO2 concentration"},
                {"class": "pm25", "unit": "µg/m³", "description": "PM2.5 particles"},
                {"class": "timestamp", "unit": "datetime", "description": "Date and time"}
            ],
            "binary_sensor_classes": [
                {"class": "motion", "on": "Detected", "off": "Clear"},
                {"class": "door", "on": "Open", "off": "Closed"},
                {"class": "window", "on": "Open", "off": "Closed"},
                {"class": "garage_door", "on": "Open", "off": "Closed"},
                {"class": "occupancy", "on": "Occupied", "off": "Clear"},
                {"class": "presence", "on": "Present", "off": "Away"},
                {"class": "smoke", "on": "Detected", "off": "Clear"},
                {"class": "moisture", "on": "Wet", "off": "Dry"},
                {"class": "vibration", "on": "Vibrating", "off": "Still"},
                {"class": "problem", "on": "Problem", "off": "OK"},
                {"class": "safety", "on": "Unsafe", "off": "Safe"},
                {"class": "connectivity", "on": "Connected", "off": "Disconnected"}
            ],
            "cover_classes": [
                {"class": "blind", "description": "Window blind"},
                {"class": "curtain", "description": "Curtain"},
                {"class": "garage", "description": "Garage door"},
                {"class": "gate", "description": "Gate"},
                {"class": "shade", "description": "Shade"},
                {"class": "shutter", "description": "Shutter"}
            ],
            "usage": "Device classes help identify entity types and expected behaviors"
        }
    
    def get_service_names_resource(self) -> Dict[str, Any]:
        """Resource providing common service names for each domain"""
        return {
            "services_by_domain": {
                "light": ["turn_on", "turn_off", "toggle", "increase_brightness", "decrease_brightness"],
                "switch": ["turn_on", "turn_off", "toggle"],
                "climate": ["set_temperature", "set_hvac_mode", "set_fan_mode", "set_preset_mode", "turn_on", "turn_off"],
                "cover": ["open_cover", "close_cover", "stop_cover", "set_cover_position", "open_cover_tilt", "close_cover_tilt"],
                "lock": ["lock", "unlock", "open"],
                "media_player": ["media_play", "media_pause", "media_stop", "media_next_track", "media_previous_track", "volume_up", "volume_down", "volume_set", "volume_mute"],
                "fan": ["turn_on", "turn_off", "toggle", "set_speed", "set_direction", "oscillate"],
                "vacuum": ["start", "stop", "pause", "return_to_base", "locate", "clean_spot"],
                "scene": ["turn_on"],
                "automation": ["trigger", "turn_on", "turn_off", "toggle", "reload"],
                "script": ["turn_on", "turn_off", "toggle", "reload"],
                "notify": ["notify", "persistent_notification"],
                "input_boolean": ["turn_on", "turn_off", "toggle"],
                "input_number": ["set_value", "increment", "decrement"],
                "input_text": ["set_value"],
                "input_select": ["select_option", "select_next", "select_previous"],
                "timer": ["start", "pause", "cancel", "finish"],
                "counter": ["increment", "decrement", "reset"]
            },
            "usage": "Use with ha_call_service tool: domain + service name (e.g., 'light.turn_on')"
        }
    
    def get_lights_on_resource(self) -> Dict[str, Any]:
        """Get all lights that are currently on"""
        result = self.get_states(domain="light")
        lights = result.get('states', [])
        lights_on = [l for l in lights if l.get('state') == 'on']
        
        return {
            "lights_on": lights_on,
            "count": len(lights_on),
            "total_lights": len(lights),
            "by_area": self._group_by_area(lights_on)
        }
    
    def get_devices_on_resource(self) -> Dict[str, Any]:
        """Get all devices that are currently on"""
        result = self.get_states()
        all_states = result.get('states', [])
        
        devices_on = []
        for entity in all_states:
            domain = entity['entity_id'].split('.')[0]
            # Check common "on" domains
            if domain in ['light', 'switch', 'fan', 'media_player', 'climate', 'vacuum']:
                if entity.get('state') in ['on', 'playing', 'heat', 'cool', 'heat_cool', 'cleaning']:
                    devices_on.append(entity)
        
        return {
            "devices_on": devices_on,
            "count": len(devices_on),
            "by_domain": self._group_by_domain(devices_on),
            "by_area": self._group_by_area(devices_on)
        }
    
    def get_temperature_sensors_resource(self) -> Dict[str, Any]:
        """Get all temperature sensors with readings"""
        result = self.get_states(domain="sensor")
        sensors = result.get('states', [])
        
        temp_sensors = []
        for sensor in sensors:
            attrs = sensor.get('attributes', {})
            # Check if it's a temperature sensor
            if attrs.get('device_class') == 'temperature' or \
               attrs.get('unit_of_measurement') in ['°C', '°F', 'celsius', 'fahrenheit']:
                temp_sensors.append({
                    "entity_id": sensor['entity_id'],
                    "name": attrs.get('friendly_name', sensor['entity_id']),
                    "temperature": sensor.get('state'),
                    "unit": attrs.get('unit_of_measurement', '°C'),
                    "area": self._get_entity_area(sensor['entity_id'])
                })
        
        return {
            "sensors": temp_sensors,
            "count": len(temp_sensors),
            "by_area": self._group_by_area(temp_sensors)
        }
    
    def get_motion_sensors_resource(self) -> Dict[str, Any]:
        """Get all motion sensors and their state"""
        result = self.get_states(domain="binary_sensor")
        sensors = result.get('states', [])
        
        motion_sensors = []
        for sensor in sensors:
            attrs = sensor.get('attributes', {})
            if attrs.get('device_class') == 'motion':
                motion_sensors.append({
                    "entity_id": sensor['entity_id'],
                    "name": attrs.get('friendly_name', sensor['entity_id']),
                    "motion_detected": sensor.get('state') == 'on',
                    "last_changed": sensor.get('last_changed'),
                    "area": self._get_entity_area(sensor['entity_id'])
                })
        
        motion_detected = [s for s in motion_sensors if s['motion_detected']]
        
        return {
            "sensors": motion_sensors,
            "count": len(motion_sensors),
            "motion_detected": motion_detected,
            "motion_detected_count": len(motion_detected),
            "by_area": self._group_by_area(motion_sensors)
        }
    
    def get_door_window_sensors_resource(self) -> Dict[str, Any]:
        """Get all door and window sensors"""
        result = self.get_states(domain="binary_sensor")
        sensors = result.get('states', [])
        
        door_window_sensors = []
        for sensor in sensors:
            attrs = sensor.get('attributes', {})
            device_class = attrs.get('device_class')
            if device_class in ['door', 'window', 'opening', 'garage_door']:
                door_window_sensors.append({
                    "entity_id": sensor['entity_id'],
                    "name": attrs.get('friendly_name', sensor['entity_id']),
                    "type": device_class,
                    "open": sensor.get('state') == 'on',
                    "area": self._get_entity_area(sensor['entity_id'])
                })
        
        open_sensors = [s for s in door_window_sensors if s['open']]
        
        return {
            "sensors": door_window_sensors,
            "count": len(door_window_sensors),
            "open": open_sensors,
            "open_count": len(open_sensors),
            "by_type": {
                "doors": [s for s in door_window_sensors if s['type'] == 'door'],
                "windows": [s for s in door_window_sensors if s['type'] == 'window'],
                "garage_doors": [s for s in door_window_sensors if s['type'] == 'garage_door']
            }
        }
    
    def get_security_status_resource(self) -> Dict[str, Any]:
        """Get security-related information"""
        result = self.get_states()
        all_states = result.get('states', [])
        
        security_info = {
            "locks": [],
            "alarms": [],
            "cameras": [],
            "motion_sensors": [],
            "door_sensors": [],
            "window_sensors": []
        }
        
        for entity in all_states:
            entity_id = entity['entity_id']
            domain = entity_id.split('.')[0]
            attrs = entity.get('attributes', {})
            device_class = attrs.get('device_class')
            
            if domain == 'lock':
                security_info['locks'].append({
                    "entity_id": entity_id,
                    "name": attrs.get('friendly_name', entity_id),
                    "locked": entity.get('state') == 'locked',
                    "area": self._get_entity_area(entity_id)
                })
            elif domain == 'alarm_control_panel':
                security_info['alarms'].append({
                    "entity_id": entity_id,
                    "name": attrs.get('friendly_name', entity_id),
                    "state": entity.get('state'),
                    "armed": entity.get('state') not in ['disarmed', 'unknown', 'unavailable']
                })
            elif domain == 'camera':
                security_info['cameras'].append({
                    "entity_id": entity_id,
                    "name": attrs.get('friendly_name', entity_id),
                    "state": entity.get('state'),
                    "recording": entity.get('state') == 'recording'
                })
            elif domain == 'binary_sensor':
                if device_class == 'motion':
                    security_info['motion_sensors'].append({
                        "entity_id": entity_id,
                        "name": attrs.get('friendly_name', entity_id),
                        "motion": entity.get('state') == 'on'
                    })
                elif device_class == 'door':
                    security_info['door_sensors'].append({
                        "entity_id": entity_id,
                        "name": attrs.get('friendly_name', entity_id),
                        "open": entity.get('state') == 'on'
                    })
                elif device_class == 'window':
                    security_info['window_sensors'].append({
                        "entity_id": entity_id,
                        "name": attrs.get('friendly_name', entity_id),
                        "open": entity.get('state') == 'on'
                    })
        
        # Calculate security summary
        unlocked_locks = [l for l in security_info['locks'] if not l['locked']]
        open_doors = [d for d in security_info['door_sensors'] if d['open']]
        open_windows = [w for w in security_info['window_sensors'] if w['open']]
        motion_detected = [m for m in security_info['motion_sensors'] if m['motion']]
        
        return {
            "status": security_info,
            "summary": {
                "all_locked": len(unlocked_locks) == 0,
                "unlocked_locks": unlocked_locks,
                "open_doors": open_doors,
                "open_windows": open_windows,
                "motion_detected": motion_detected,
                "secure": len(unlocked_locks) == 0 and len(open_doors) == 0 and len(open_windows) == 0
            }
        }
    
    def get_climate_status_resource(self) -> Dict[str, Any]:
        """Get climate control status"""
        result = self.get_states()
        all_states = result.get('states', [])
        
        climate_info = {
            "thermostats": [],
            "temperature_sensors": [],
            "humidity_sensors": [],
            "air_quality": []
        }
        
        for entity in all_states:
            entity_id = entity['entity_id']
            domain = entity_id.split('.')[0]
            attrs = entity.get('attributes', {})
            device_class = attrs.get('device_class')
            
            if domain == 'climate':
                climate_info['thermostats'].append({
                    "entity_id": entity_id,
                    "name": attrs.get('friendly_name', entity_id),
                    "mode": entity.get('state'),
                    "current_temperature": attrs.get('current_temperature'),
                    "target_temperature": attrs.get('temperature'),
                    "area": self._get_entity_area(entity_id)
                })
            elif domain == 'sensor':
                if device_class == 'temperature':
                    climate_info['temperature_sensors'].append({
                        "entity_id": entity_id,
                        "name": attrs.get('friendly_name', entity_id),
                        "temperature": entity.get('state'),
                        "unit": attrs.get('unit_of_measurement'),
                        "area": self._get_entity_area(entity_id)
                    })
                elif device_class == 'humidity':
                    climate_info['humidity_sensors'].append({
                        "entity_id": entity_id,
                        "name": attrs.get('friendly_name', entity_id),
                        "humidity": entity.get('state'),
                        "area": self._get_entity_area(entity_id)
                    })
                elif device_class in ['pm25', 'co2', 'aqi']:
                    climate_info['air_quality'].append({
                        "entity_id": entity_id,
                        "name": attrs.get('friendly_name', entity_id),
                        "type": device_class,
                        "value": entity.get('state'),
                        "unit": attrs.get('unit_of_measurement')
                    })
        
        return climate_info
    
    def get_battery_status_resource(self) -> Dict[str, Any]:
        """Get battery levels for all devices"""
        result = self.get_states()
        all_states = result.get('states', [])
        
        battery_devices = []
        for entity in all_states:
            attrs = entity.get('attributes', {})
            # Check for battery level attribute
            battery_level = attrs.get('battery_level') or attrs.get('battery')
            if battery_level is not None:
                battery_devices.append({
                    "entity_id": entity['entity_id'],
                    "name": attrs.get('friendly_name', entity['entity_id']),
                    "battery_level": battery_level,
                    "low": battery_level < 20 if isinstance(battery_level, (int, float)) else False
                })
            # Also check for battery sensors
            elif entity['entity_id'].split('.')[0] == 'sensor' and attrs.get('device_class') == 'battery':
                try:
                    level = float(entity.get('state', 0))
                    battery_devices.append({
                        "entity_id": entity['entity_id'],
                        "name": attrs.get('friendly_name', entity['entity_id']),
                        "battery_level": level,
                        "low": level < 20
                    })
                except:
                    pass
        
        low_battery = [d for d in battery_devices if d['low']]
        
        return {
            "devices": battery_devices,
            "count": len(battery_devices),
            "low_battery": low_battery,
            "low_battery_count": len(low_battery)
        }
    
    # Helper methods for grouping
    def _group_by_domain(self, entities: List[Dict]) -> Dict[str, List[Dict]]:
        """Group entities by domain"""
        grouped = {}
        for entity in entities:
            entity_id = entity.get('entity_id', '')
            domain = entity_id.split('.')[0] if entity_id else 'unknown'
            if domain not in grouped:
                grouped[domain] = []
            grouped[domain].append(entity)
        return grouped
    
    def _group_by_area(self, entities: List[Dict]) -> Dict[str, List[Dict]]:
        """Group entities by area"""
        grouped = {}
        for entity in entities:
            area = entity.get('area') or 'No Area'
            if area not in grouped:
                grouped[area] = []
            grouped[area].append(entity)
        return grouped
    
    def _get_entity_area(self, entity_id: str) -> Optional[str]:
        """Get the area for an entity"""
        try:
            entities = self.get_entities()
            for entity in entities:
                if entity.get('entity_id') == entity_id:
                    area_id = entity.get('area_id')
                    if area_id:
                        areas = self.get_areas()
                        for area in areas:
                            if area.get('area_id') == area_id:
                                return area.get('name')
        except:
            pass
        return None