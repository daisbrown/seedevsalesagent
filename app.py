from flask import Flask, request, session, render_template, jsonify
from openai import AzureOpenAI
import base64
from PIL import Image as PILImage
from io import BytesIO
from flask_sqlalchemy import SQLAlchemy
from uuid import uuid4
import re
import html
from typing import Tuple, Optional
import requests
import json
import os
from dotenv import load_dotenv
import random
import datetime
from werkzeug.utils import secure_filename
import mimetypes
from azure.storage.blob import BlobServiceClient, BlobClient
from azure.identity import DefaultAzureCredential
from azure.core.exceptions import ResourceNotFoundError
import urllib.parse
import traceback
from threading import Lock
from sqlalchemy.exc import SQLAlchemyError
from config import FOOD_SYSTEM_PROMPT, PROTECTIVE_SYSTEM_PROMPT
from copy import deepcopy
import logging
from jwt import PyJWTError
import jwt
from services.email_service import EmailService
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    logger.debug(f"Loading environment variables from: {env_path}")
    load_dotenv(env_path, override=True)  # Add override=True here
    logger.debug("Environment variables loaded successfully")
else:
    logger.warning(f"No .env file found at {env_path}, using existing environment variables")

class Config:
    """Configuration class that loads and validates environment variables."""
    
    @classmethod
    def get_env(cls, key: str, default=None, required=False, var_type=str):
        """
        Get environment variable with type conversion and validation.
        """
        value = os.environ.get(key, default)
        
        if value is None and required:
            raise ValueError(f"Required environment variable '{key}' is not set")
            
        if value is not None and var_type != str:
            try:
                value = var_type(value)
            except (ValueError, TypeError) as e:
                raise ValueError(f"Environment variable '{key}' has invalid type. Expected {var_type.__name__}, got: {value}")
                
        return value

    @classmethod
    def initialize(cls):
        # Flask configuration
        cls.FLASK_SECRET_KEY = cls.get_env('FLASK_SECRET_KEY', os.urandom(24))
        
        # Debug configuration
        cls.DEBUG_USER_ID = cls.get_env('DEBUG_USER_ID', 'local-dev-user')
        cls.DEBUG_USER_EMAIL = cls.get_env('DEBUG_USER_EMAIL', 'local-dev@example.com')
        cls.DEBUG_USER_GROUPS = cls.get_env('DEBUG_USER_GROUPS', '15214a1b-5659-4511-910c-78c247d45dae')
        cls.DEBUG_MODE = cls.get_env('FLASK_DEBUG', default=False, var_type=bool)
        
        # Local development configuration
        cls.IS_LOCAL_DEV = cls.get_env('IS_LOCAL_DEV', default=False, var_type=bool)
        
        # Azure OpenAI Configuration
        cls.AZURE_OPENAI_ENDPOINT = cls.get_env('AZURE_OPENAI_ENDPOINT', required=True)
        cls.AZURE_OPENAI_KEY = cls.get_env('AZURE_OPENAI_KEY', required=True)
        cls.AZURE_OPENAI_API_VERSION = cls.get_env('AZURE_OPENAI_API_VERSION', '2024-02-15-preview')
        cls.AZURE_OPENAI_DEPLOYMENT = cls.get_env('AZURE_OPENAI_DEPLOYMENT', required=True)
        cls.AZURE_OPENAI_MESSAGE_HISTORY_LIMIT = cls.get_env('AZURE_OPENAI_MESSAGE_HISTORY_LIMIT', 10, var_type=int)
        cls.AZURE_OPENAI_TEMPERATURE = cls.get_env('AZURE_OPENAI_TEMPERATURE', 0.7, var_type=float)
        
        # Token limits
        cls.AZURE_OPENAI_MAX_COMPLETION_TOKENS = cls.get_env('AZURE_OPENAI_MAX_COMPLETION_TOKENS', 4096, var_type=int)
        cls.AZURE_OPENAI_MAX_TOTAL_TOKENS = cls.get_env('AZURE_OPENAI_MAX_TOTAL_TOKENS', 500000, var_type=int)
        cls.AZURE_OPENAI_MAX_PROMPT_TOKENS = cls.get_env('AZURE_OPENAI_MAX_PROMPT_TOKENS', 490000, var_type=int)
        
        # Azure AI Search Configuration
        cls.AZURE_AI_SEARCH_ENDPOINT = cls.get_env('AZURE_AI_SEARCH_ENDPOINT', required=True)
        cls.AZURE_AI_SEARCH_KEY = cls.get_env('AZURE_AI_SEARCH_KEY', required=True)
        cls.AZURE_AI_SEARCH_TOP_N_DOCS = cls.get_env('AZURE_AI_SEARCH_TOP_N_DOCS', 5, var_type=int)
        cls.AZURE_OPENAI_SEARCH_INDEX = cls.get_env('AZURE_OPENAI_SEARCH_INDEX', required=True)
        cls.AZURE_OPENAI_SEARCH_SALESREP_INDEX = cls.get_env('AZURE_OPENAI_SEARCH_SALESREP_INDEX', required=True)
        
        # Group Object IDs
        cls.SALES_GENERAL_GROUP_ID = cls.get_env('SALES_GENERAL_GROUP_ID', '15214a1b-5659-4511-910c-78c247d45dae')
        cls.SALES_SPECIAL_GROUP_ID = cls.get_env('SALES_SPECIAL_GROUP_ID', 'b8512a5e-9155-4f2d-bff8-1a5d660c4bbb')
        cls.SALES_MANAGEMENT_GROUP_ID = cls.get_env('SALES_MANAGEMENT_GROUP_ID', 'ceb36a9c-0dd8-448c-9d39-627835c30902')
        
        # Azure AI Search Indexes - supporting comma-separated lists in the env variable
        cls.SALES_GENERAL_INDEX = [x.strip() for x in cls.get_env('SALES_GENERAL_INDEX', 'sales-vector').split(',')]
        cls.SALES_SPECIAL_INDEX = [x.strip() for x in cls.get_env('SALES_SPECIAL_INDEX', 'cash-telemetry-vector').split(',')]
        cls.SALES_MANAGEMENT_INDEX = [x.strip() for x in cls.get_env('SALES_MANAGEMENT_INDEX', 'cash-feedback-vector').split(',')]
        
        # Group to Index Mapping using Object IDs
        try:
            # Combine all accessible indexes for each group level
            cls.GROUP_TO_INDEX_MAP = json.loads(cls.get_env('GROUP_TO_INDEX_MAP', f'''{{
                "{cls.SALES_GENERAL_GROUP_ID}": {json.dumps(cls.SALES_GENERAL_INDEX)},
                "{cls.SALES_SPECIAL_GROUP_ID}": {json.dumps(cls.SALES_GENERAL_INDEX + cls.SALES_SPECIAL_INDEX)},
                "{cls.SALES_MANAGEMENT_GROUP_ID}": {json.dumps(cls.SALES_GENERAL_INDEX + cls.SALES_SPECIAL_INDEX + cls.SALES_MANAGEMENT_INDEX)}
            }}'''))
        except json.JSONDecodeError:
            logger.error("Invalid GROUP_TO_INDEX_MAP configuration")
        
        # Azure Blob Storage Configuration
        logger.debug("Loading Azure Blob Storage Configuration...")
        cls.AZURE_STORAGE_ACCOUNT = cls.get_env('AZURE_STORAGE_ACCOUNT', required=True)
        cls.AZURE_STORAGE_CONTAINER_NAME = cls.get_env('AZURE_STORAGE_CONTAINER_NAME', required=True)
        cls.AZURE_STORAGE_CONTAINER_TELEMETRY_NAME = cls.get_env('AZURE_STORAGE_CONTAINER_TELEMETRY_NAME', required=True)
        cls.AZURE_STORAGE_CONTAINER_FEEDBACK_NAME = cls.get_env('AZURE_STORAGE_CONTAINER_FEEDBACK_NAME', required=True)
        logger.debug(f"Loaded AZURE_STORAGE_CONTAINER_FEEDBACK_NAME: {cls.AZURE_STORAGE_CONTAINER_FEEDBACK_NAME}")
        logger.debug("Config.initialize() completed")
        
        # Application Insights Configuration
        cls.APPLICATIONINSIGHTS_CONNECTION_STRING = cls.get_env('APPLICATIONINSIGHTS_CONNECTION_STRING')
        
        # Chat Control Configuration
        cls.EMPTY_CHAT_TIMEOUT = cls.get_env('EMPTY_CHAT_TIMEOUT', 3600, var_type=int)
        cls.SALES_DATA_REFRESH_INTERVAL = cls.get_env('SALES_DATA_REFRESH_INTERVAL_SECONDS', 3600, var_type=int)
        
    @classmethod
    def log_config(cls):
        """Log the current configuration (excluding sensitive values)."""
        sensitive_keys = {'AZURE_OPENAI_KEY', 'AZURE_AI_SEARCH_KEY', 'FLASK_SECRET_KEY', 
                         'APPLICATIONINSIGHTS_CONNECTION_STRING'}
        
        logger.info("Current configuration:")
        for key in dir(cls):
            if not key.startswith('_') and key.isupper():
                value = getattr(cls, key)
                if key in sensitive_keys:
                    value = '********'
                logger.info(f"{key}: {value}")

# Initialize the configuration
Config.initialize()

# Log current configuration (excluding sensitive values)
Config.log_config()

app = Flask(__name__)

# Configure database path to be in a writable location in Azure Web Apps
db_dir = os.path.join(os.environ.get('HOME', ''), 'site', 'wwwroot')
if not os.path.exists(db_dir):
    os.makedirs(db_dir, exist_ok=True)
db_path = os.path.join(db_dir, 'chat_sessions.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Use secret key from config
app.secret_key = Config.FLASK_SECRET_KEY

# Add configuration for message history limit and temperature
MESSAGE_HISTORY_LIMIT = Config.AZURE_OPENAI_MESSAGE_HISTORY_LIMIT
TEMPERATURE = Config.AZURE_OPENAI_TEMPERATURE

# Add chat control configurations
EMPTY_CHAT_TIMEOUT = Config.EMPTY_CHAT_TIMEOUT

# Add these configurations at the top with other constants
MAX_TOKENS = {
    'COMPLETION': Config.AZURE_OPENAI_MAX_COMPLETION_TOKENS,
    'TOTAL': Config.AZURE_OPENAI_MAX_TOTAL_TOKENS,
    'PROMPT': Config.AZURE_OPENAI_MAX_PROMPT_TOKENS,
    'WARNING_THRESHOLD': 0.9  # Warn at 90% usage
}

# Create the db instance without the app
db = SQLAlchemy()

# Add this new model class
class ChatSession(db.Model):
    __tablename__ = 'chat_session'  # Explicitly set the table name
    id = db.Column(db.String(36), primary_key=True)
    messages = db.Column(db.JSON)  # Stores the full message history including system prompts
    chat_history = db.Column(db.JSON)  # Stores user-visible chat history with citations
    citations = db.Column(db.JSON)  # Store citations for each message
    product_category = db.Column(db.String(50))  # Instapak, Autobag, Shrink Solutions, or Sales Strategy
    confidence_level = db.Column(db.Integer)  # 1-10 confidence rating
    focus_area = db.Column(db.String(100))  # Specific aspect of product/sales being discussed
    detected_language = db.Column(db.String(50))  # Language detection for multilingual support
    created_at = db.Column(db.String(50), default=lambda: datetime.datetime.utcnow().isoformat())
    last_activity = db.Column(db.String(50), default=lambda: datetime.datetime.utcnow().isoformat())  # Track last activity time
    user_id = db.Column(db.String(50))  # Add this field to store user ID
    sales_metadata = db.Column(db.JSON)  # Store sales rep metadata

class SalesDataCache:
    """Thread-safe cache for sales representative data with size limits and LRU eviction."""
    
    def __init__(self, max_size: int = 1000, refresh_interval: int = 3600):
        self.cache = {}
        self.last_refresh = {}
        self.access_times = {}  # For LRU tracking
        self.max_size = max_size
        self.refresh_interval = refresh_interval
        self.lock = Lock()
        
    def _is_valid(self, key: str) -> bool:
        """Check if cached data is still valid."""
        if key not in self.cache or key not in self.last_refresh:
            return False
        age = (datetime.datetime.utcnow() - self.last_refresh[key]).total_seconds()
        return age < self.refresh_interval
        
    def _evict_lru(self):
        """Evict least recently used items when cache is full."""
        if not self.access_times:
            return
        # Find oldest accessed key
        oldest_key = min(self.access_times.items(), key=lambda x: x[1])[0]
        # Remove from all tracking dicts
        self.cache.pop(oldest_key, None)
        self.last_refresh.pop(oldest_key, None)
        self.access_times.pop(oldest_key, None)
        
    def get(self, key: str) -> Optional[dict]:
        """Get data from cache if valid."""
        with self.lock:
            if self._is_valid(key):
                self.access_times[key] = datetime.datetime.utcnow()
                return self.cache[key]
            return None
            
    def set(self, key: str, data: dict) -> None:
        """Store data in cache with eviction if needed."""
        with self.lock:
            # Evict LRU if at capacity
            if len(self.cache) >= self.max_size:
                self._evict_lru()
            
            now = datetime.datetime.utcnow()
            self.cache[key] = data
            self.last_refresh[key] = now
            self.access_times[key] = now
            
    def invalidate(self, key: str) -> None:
        """Remove specific key from cache."""
        with self.lock:
            self.cache.pop(key, None)
            self.last_refresh.pop(key, None)
            self.access_times.pop(key, None)
            
    def clear(self) -> None:
        """Clear entire cache."""
        with self.lock:
            self.cache.clear()
            self.last_refresh.clear()
            self.access_times.clear()

# Initialize the cache with configuration values
sales_data_cache = SalesDataCache(
    max_size=1000,  # Adjust based on expected number of concurrent users
    refresh_interval=Config.SALES_DATA_REFRESH_INTERVAL
)
# Add these constants at the top with other configurations
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB
ALLOWED_MIME_TYPES = {'image/jpeg', 'image/png'}

# Initialize the email service after app creation
email_service = EmailService(Config)

# Add this code block before running the app
db.init_app(app)

# Initialize database tables
def init_db():
    try:
        with app.app_context():
            # Check if tables exist before creating
            inspector = db.inspect(db.engine)
            existing_tables = inspector.get_table_names()
            
            # Only create tables if they don't exist
            if 'chat_session' not in existing_tables:
                logger.info("Creating database tables...")
                db.create_all()
                logger.info("Tables created successfully")
            else:
                logger.info("Database tables already exist, skipping creation")
                
    except Exception as e:
        logger.error(f"Failed to initialize database: {str(e)}")
        logger.error(traceback.format_exc())
        raise

init_db()


def has_sales_rep_data(metadata):
    """Check if metadata contains actual sales rep data rather than default values"""
    if not metadata:
        return False
        
    # Check if we have any orders data
    orders_data = metadata.get('orders', [])
    if not orders_data:
        return False
    
    # Check if we have aggregate metrics
    has_aggregate_data = (
        isinstance(metadata.get('total_orders'), int) and metadata['total_orders'] > 0 and
        isinstance(metadata.get('execution_status'), list) and len(metadata['execution_status']) > 0
    )
    
    # Check if we have sales metrics
    has_sales_data = (
        isinstance(metadata.get('sales_documents'), dict) and
        (metadata['sales_documents'].get('total_value_usd', 0) > 0 or 
         metadata['sales_documents'].get('total_value_dc', 0) > 0)
    )
    
    # Check if we have territory/organizational data
    has_territory_data = (
        isinstance(metadata.get('territories'), dict) and
        any([
            metadata['territories'].get('companies', []),
            metadata['territories'].get('sales_orgs', []),
            metadata['territories'].get('plants', []),
            metadata['territories'].get('divisions', [])
        ])
    )
    
    # Check if we have customer data
    has_customer_data = (
        isinstance(metadata.get('customer_data'), dict) and
        any([
            metadata['customer_data'].get('sold_to_parties', []),
            metadata['customer_data'].get('ship_to_parties', []),
            metadata['customer_data'].get('countries', []),
            metadata['customer_data'].get('regions', [])
        ])
    )
    
    # Return True only if we have orders and at least one other type of valid data
    return bool(orders_data) and any([
        has_aggregate_data,
        has_sales_data,
        has_territory_data,
        has_customer_data
    ])


def get_system_prompt_with_sales_context(base_prompt: str, sales_context: dict) -> str:
    if not has_sales_rep_data(sales_context):
        return base_prompt

    # Add current date
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    context_sections = []

     # Add aggregated metrics section
    metrics_section = f"\nSales Overview as of {current_date}:\n\n"
    metrics_json = {
        "metrics": {
            "total_orders": sales_context.get('total_orders', 0),
            "blocked_orders": sales_context.get('blocked_orders', 0),
            "total_order_quantity": sales_context.get('total_order_quantity', 0),
            "total_open_quantity": sales_context.get('total_open_quantity', 0),
            "execution_status": sales_context.get('execution_status', []),  
            "stock_availability": sales_context.get('stock_availability', {
                "claimed": 0,
                "not_claimed": 0
            }),
            "sales_documents": {  # Changed from sales_metrics
                "total_value_usd": sales_context.get('sales_documents', {}).get('total_value_usd', 0),
                "total_value_dc": sales_context.get('sales_documents', {}).get('total_value_dc', 0),
                "types": sales_context.get('sales_documents', {}).get('types', [])  
            },
            "territories": {
                "companies": sales_context.get('territories', {}).get('companies', []),
                "sales_orgs": sales_context.get('territories', {}).get('sales_orgs', []),
                "plants": sales_context.get('territories', {}).get('plants', []),
                "divisions": sales_context.get('territories', {}).get('divisions', [])
            },
            "customer_data": {
                "unique_sold_to": len(sales_context.get('customer_data', {}).get('sold_to_parties', [])),
                "unique_ship_to": len(sales_context.get('customer_data', {}).get('ship_to_parties', [])),
                "countries": sales_context.get('customer_data', {}).get('countries', []),
                "regions": sales_context.get('customer_data', {}).get('regions', [])
            },
            "delivery_metrics": {
                "on_time": sales_context.get('delivery_metrics', {}).get('on_time', 0),
                "delayed": sales_context.get('delivery_metrics', {}).get('delayed', 0),
                "reliability_scores": sales_context.get('delivery_metrics', {}).get('reliability_scores', [])
            }
        }
    }
    metrics_section += json.dumps(metrics_json, indent=2)
    context_sections.append(metrics_section)
 
    # Add recent orders detail JSON format
    if sales_context.get('orders'):
        orders_section = "\nDetailed Order Information as of " + current_date + ":\n\n"
        # Create orders list in JSON format
        orders_json = {"orders": []}
        
        for order in sales_context['orders']:
            customer_info = order.get('customer_info', {})
            delivery_info = order.get('delivery_info', {})
            product_info = order.get('product_info', {})
            credit_status = order.get('credit_status', {})
            
            # Format value_usd with comma separators and 2 decimal places
            value_usd = order.get('value_usd', 0)
            formatted_value_usd = "${:,.2f}".format(float(value_usd)) if value_usd else "$0.00"
            
            order_data = {
                "order_number": order.get('order_number', 'N/A'),
                "execution_status": order.get('execution_status', 'Unknown'),
                "customer_classification": order.get('customer_classification', 'Unknown'),
                "blocked_header": order.get('blocked_header', 'N/A'),
                "order_quantity": order.get('order_quantity', 0),
                "open_quantity": order.get('open_quantity', 0),
                "value_usd": formatted_value_usd,
                "value_dc": order.get('value_dc', 0),
                "document_currency": order.get('document_currency', 'N/A'),
                "payment_terms": order.get('payment_terms', 'N/A'),
                "incoterms": order.get('incoterms', 'N/A'),
                "stock_claimed": order.get('stock_claimed', 'N/A'),
                "delivery_number": order.get('delivery_number', 'N/A'),
                "delivery_created_on": order.get('delivery_created_on', 'N/A'),
                "sales_doc_type": order.get('sales_doc_type', 'N/A'),
                "company_code": order.get('company_code', 'N/A'),
                "sales_org": order.get('sales_org', 'N/A'),
                "order_status": order.get('order_status', 'N/A'),
                "delivery_reliability": order.get('delivery_reliability', 'N/A'),
                "customer_info": {
                    "sold_to": customer_info.get('sold_to', 'Unknown'),
                    "ship_to": customer_info.get('ship_to', 'Unknown'),
                    "ship_to_country": customer_info.get('ship_to_country', 'Unknown'),
                    "ship_to_region": customer_info.get('ship_to_region', 'Unknown'),
                    "ship_to_state": customer_info.get('ship_to_state', 'Unknown'),
                    "sold_to_region_state": customer_info.get('sold_to_region_state', 'Unknown'),
                    "purchase_order": customer_info.get('purchase_order', 'N/A'),
                    "po_date": customer_info.get('po_date', 'N/A'),
                    "po_type": customer_info.get('po_type', 'N/A')
                },
                "delivery_info": delivery_info,  # Already correctly structured
                "product_info": {
                    **product_info,  # Keep existing fields
                    "claimed_stock_quantity": order.get('product_info', {}).get('claimed_stock_quantity', 'N/A')
                },
                "status_info": {
                    "overall_status": order.get('status_info', {}).get('overall_status', 'N/A'),
                    "overall_status_text": order.get('status_info', {}).get('overall_status_text', 'N/A'),
                    "delivery_status": order.get('status_info', {}).get('delivery_status', 'N/A'),
                    "rejection_status": order.get('status_info', {}).get('rejection_status', 'N/A')
                },
                "sales_team": {
                    "sales_employee": order.get('sales_team', {}).get('sales_employee', 'N/A'),
                    "sales_emp_key": order.get('sales_team', {}).get('sales_emp_key', 'N/A'),
                    "credit_rep": order.get('sales_team', {}).get('credit_rep', 'N/A'),
                    "customer_service_representative": order.get('sales_team', {}).get('customer_service_representative', 'N/A'),
                    "created_by": order.get('sales_team', {}).get('created_by', 'N/A'),
                    "sales_district": order.get('sales_team', {}).get('sales_district', 'N/A'),
                    "gid": order.get('sales_team', {}).get('gid', 'N/A')
                },
                "additional_info": {
                    "payment_terms": order.get('additional_info', {}).get('payment_terms', 'N/A'),
                    "incoterms": order.get('additional_info', {}).get('incoterms', 'N/A'),
                    "document_currency": order.get('additional_info', {}).get('document_currency', 'N/A'),
                    "reference_line": order.get('additional_info', {}).get('reference_line', 'N/A'),
                    "reference_order": order.get('additional_info', {}).get('reference_order', 'N/A'),
                    "quantity_closed": order.get('additional_info', {}).get('quantity_closed', 'N/A'),
                    "cumulative_confirmed_qty": order.get('additional_info', {}).get('cumulative_confirmed_qty', 'N/A'),
                    "sales_order_item_value": order.get('additional_info', {}).get('sales_order_item_value', 'N/A'),
                    "open_sales_value": order.get('additional_info', {}).get('open_sales_value', 'N/A'),
                    "total_sales_order_value": order.get('additional_info', {}).get('total_sales_order_value', 'N/A'),
                    "created_on": order.get('additional_info', {}).get('created_on', 'N/A')
                }
            }
            
            orders_json["orders"].append(order_data)
            
        # Convert to formatted JSON string and add to section
        orders_section += json.dumps(orders_json, indent=2)
        # Add the order section to context_sections
        context_sections.append(orders_section)

    # Combine all sections
    combined_context = "\n".join(context_sections)
    
    # Replace the placeholder with the formatted context
    return base_prompt.replace("[SALES REP CONTEXT HERE]", combined_context)

def get_cached_sales_rep_data(email: str) -> Optional[dict]:
    """Get sales rep data from cache or fetch if expired/missing."""
    # Try to get from cache first
    cached_data = sales_data_cache.get(email)
    if cached_data is not None:
        logger.debug(f"Using cached sales data for {email}")
        return cached_data
    
    # If not in cache or expired, fetch new data
    logger.debug(f"Fetching fresh sales data for {email}")
    try:
        user_groups = get_user_groups_from_headers()
        search_results = orchestrate_federated_search("*", user_groups, email)
        
        # Get sales data from first result
        if search_results and search_results[0].get('_index_name') in Config.SALES_GENERAL_INDEX:
            sales_data = search_results[0]
            # Update cache with new data
            sales_data_cache.set(email, sales_data)
            logger.debug(f"Updated sales data cache for {email}")
            return sales_data
            
    except Exception as e:
        logger.error(f"Error fetching sales rep data: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Don't cache errors, return None
        return None
    
    return None

@app.route('/food')
def food():
     # Check for authentication
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID')
    user_email = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME')
    
    # Add local development bypass using environment variables
    if Config.IS_LOCAL_DEV:
        user_id = user_id or Config.get_env('DEBUG_USER_ID', 'local-dev-user')
        user_email = user_email or Config.get_env('DEBUG_USER_EMAIL', 'local-dev@example.com')
    
    if not user_id or not user_email:
        logger.error("Missing authentication headers")
        logger.debug(f"Headers: {dict(request.headers)}")
        return "Authentication required", 401
    
    # Initialize metadata with defaults
    metadata = {
        'Email': user_email if user_email else "Not Available",
        'Phone': "Not Available",
        'total_orders': 0,
        'blocked_orders': 0,
        'total_order_quantity': 0,
        'total_open_quantity': 0,
        'execution_status': [],
        'stock_availability': {
            'claimed': 0,
            'not_claimed': 0
        },
        'sales_documents': {
            'types': [],
            'total_value_usd': 0.0,
            'total_value_dc': 0.0
        },
        'delivery_metrics': {
            'on_time': 0,
            'delayed': 0,
            'reliability_scores': []
        },
        'territories': {
            'companies': [],
            'sales_orgs': [],
            'plants': [],
            'divisions': []
        },
        'customer_data': {
            'sold_to_parties': [],
            'ship_to_parties': [],
            'countries': [],
            'regions': []
        },
        'orders': []
    }
    
    try:
        # Get user groups for federated search
        user_groups = get_user_groups_from_headers()
        
        # Perform federated search to get full context
        search_results = orchestrate_federated_search("*", user_groups, user_email)
        
        if search_results:
            # Organize results by index
            index_data = {}
            for result in search_results:
                if result.get('_index_name') in Config.SALES_GENERAL_INDEX:
                    # Update metadata with sales data
                    metadata.update(result)
                else:
                    # Store other results in index_data
                    index_name = result.pop('_index_name', 'unknown_index')
                    index_data[index_name] = result
            
            # Store the organized data in metadata
            metadata['index_data'] = index_data
            
            # Store additional search results in metadata for context
            metadata['additional_context'] = []
            for result in search_results[1:3]:  # Store up to 2 additional results
                if result.get('_index_name') not in Config.SALES_GENERAL_INDEX:
                    context_data = {}
                    for key, value in result.items():
                        if key not in ['_federated_score', '_index_name']:
                            context_data[key] = value
                    if context_data:
                        metadata['additional_context'].append(context_data)
            
            # Update cache with new data
            sales_data_cache.set(user_email, metadata)
            
    except Exception as e:
        logger.error(f"Error retrieving sales rep data: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Continue with default metadata
    
    # Filter chat sessions by user_id
    all_chats = ChatSession.query.filter_by(user_id=user_id).order_by(ChatSession.created_at.desc()).all()
    
    # Get current session_id safely
    current_session_id = session.get('session_id')
    
    conversations = [{
        'id': chat.id,
        'title': chat.focus_area or 'New Conversation',
        'time': datetime.datetime.utcnow().fromisoformat(chat.created_at).strftime('%I:%M %p') if chat.created_at else '',
        'active': chat.id == current_session_id
    } for chat in all_chats]

    # Create new session if none exists
    if not current_session_id:
        session['session_id'] = str(uuid4())
        chat_session = initialize_chat_session(session['session_id'], user_id, metadata)
        db.session.add(chat_session)
        safe_commit()
    else:
        # Verify the session exists and belongs to this user
        chat_session = db.session.get(ChatSession, current_session_id)
        if not chat_session or chat_session.user_id != user_id:
            # Invalid or unauthorized session, create new one
            session['session_id'] = str(uuid4())
            chat_session = initialize_chat_session(session['session_id'], user_id, metadata)
            db.session.add(chat_session)
            safe_commit()
        else:
            # Update existing session's metadata with new data
            chat_session.sales_metadata = metadata
            safe_commit()

    # Get current chat session data
    chat_session = db.session.get(ChatSession, session['session_id'])
    
    chat_data = {
        'chat_history': chat_session.chat_history,
        'conversations': conversations,
        'confidence_level': chat_session.confidence_level or 0,
        'product_category': chat_session.product_category or "",
        'detected_language': chat_session.detected_language or "",
        'focus_area': chat_session.focus_area or "New Conversation",
        'metadata': chat_session.sales_metadata
    }

    return render_template("food.html", user_name=user_email, user_id=user_id, **chat_data)

@app.route('/protective')
def protective():
     # Check for authentication
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID')
    user_email = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME')
    
    # Add local development bypass using environment variables
    if Config.IS_LOCAL_DEV:
        user_id = user_id or Config.get_env('DEBUG_USER_ID', 'local-dev-user')
        user_email = user_email or Config.get_env('DEBUG_USER_EMAIL', 'local-dev@example.com')
    
    if not user_id or not user_email:
        logger.error("Missing authentication headers")
        logger.debug(f"Headers: {dict(request.headers)}")
        return "Authentication required", 401
    
    # Initialize metadata with defaults
    metadata = {
        'Email': user_email if user_email else "Not Available",
        'Phone': "Not Available",
        'total_orders': 0,
        'blocked_orders': 0,
        'total_order_quantity': 0,
        'total_open_quantity': 0,
        'execution_status': [],
        'stock_availability': {
            'claimed': 0,
            'not_claimed': 0
        },
        'sales_documents': {
            'types': [],
            'total_value_usd': 0.0,
            'total_value_dc': 0.0
        },
        'delivery_metrics': {
            'on_time': 0,
            'delayed': 0,
            'reliability_scores': []
        },
        'territories': {
            'companies': [],
            'sales_orgs': [],
            'plants': [],
            'divisions': []
        },
        'customer_data': {
            'sold_to_parties': [],
            'ship_to_parties': [],
            'countries': [],
            'regions': []
        },
        'orders': []
    }
    
    try:
        # Get user groups for federated search
        user_groups = get_user_groups_from_headers()
        
        # Perform federated search to get full context
        search_results = orchestrate_federated_search("*", user_groups, user_email)
        
        if search_results:
            # Organize results by index
            index_data = {}
            for result in search_results:
                if result.get('_index_name') in Config.SALES_GENERAL_INDEX:
                    # Update metadata with sales data
                    metadata.update(result)
                else:
                    # Store other results in index_data
                    index_name = result.pop('_index_name', 'unknown_index')
                    index_data[index_name] = result
            
            # Store the organized data in metadata
            metadata['index_data'] = index_data
            
            # Store additional search results in metadata for context
            metadata['additional_context'] = []
            for result in search_results[1:3]:  # Store up to 2 additional results
                if result.get('_index_name') not in Config.SALES_GENERAL_INDEX:
                    context_data = {}
                    for key, value in result.items():
                        if key not in ['_federated_score', '_index_name']:
                            context_data[key] = value
                    if context_data:
                        metadata['additional_context'].append(context_data)
            
            # Update cache with new data
            sales_data_cache.set(user_email, metadata)
            
    except Exception as e:
        logger.error(f"Error retrieving sales rep data: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Continue with default metadata
    
    # Filter chat sessions by user_id
    all_chats = ChatSession.query.filter_by(user_id=user_id).order_by(ChatSession.created_at.desc()).all()
    
    # Get current session_id safely
    current_session_id = session.get('session_id')
    
    conversations = [{
        'id': chat.id,
        'title': chat.focus_area or 'New Conversation',
        'time': datetime.datetime.utcnow().fromisoformat(chat.created_at).strftime('%I:%M %p') if chat.created_at else '',
        'active': chat.id == current_session_id
    } for chat in all_chats]

    # Create new session if none exists
    if not current_session_id:
        session['session_id'] = str(uuid4())
        chat_session = initialize_chat_session(session['session_id'], user_id, metadata)
        db.session.add(chat_session)
        safe_commit()
    else:
        # Verify the session exists and belongs to this user
        chat_session = db.session.get(ChatSession, current_session_id)
        if not chat_session or chat_session.user_id != user_id:
            # Invalid or unauthorized session, create new one
            session['session_id'] = str(uuid4())
            chat_session = initialize_chat_session(session['session_id'], user_id, metadata)
            db.session.add(chat_session)
            safe_commit()
        else:
            # Update existing session's metadata with new data
            chat_session.sales_metadata = metadata
            safe_commit()

    # Get current chat session data
    chat_session = db.session.get(ChatSession, session['session_id'])
    
    chat_data = {
        'chat_history': chat_session.chat_history,
        'conversations': conversations,
        'confidence_level': chat_session.confidence_level or 0,
        'product_category': chat_session.product_category or "",
        'detected_language': chat_session.detected_language or "",
        'focus_area': chat_session.focus_area or "New Conversation",
        'metadata': chat_session.sales_metadata
    }

    return render_template("protective.html", user_name=user_email, user_id=user_id, **chat_data)

@app.route('/', methods=['GET'])
def index():
    # Check for authentication
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID')
    user_email = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME')
    
    # Add local development bypass using environment variables
    if Config.IS_LOCAL_DEV:
        user_id = user_id or Config.get_env('DEBUG_USER_ID', 'local-dev-user')
        user_email = user_email or Config.get_env('DEBUG_USER_EMAIL', 'local-dev@example.com')
    
    if not user_id or not user_email:
        logger.error("Missing authentication headers")
        logger.debug(f"Headers: {dict(request.headers)}")
        return "Authentication required", 401
    
    # Initialize metadata with defaults
    metadata = {
        'Email': user_email if user_email else "Not Available",
        'Phone': "Not Available",
        'total_orders': 0,
        'blocked_orders': 0,
        'total_order_quantity': 0,
        'total_open_quantity': 0,
        'execution_status': [],
        'stock_availability': {
            'claimed': 0,
            'not_claimed': 0
        },
        'sales_documents': {
            'types': [],
            'total_value_usd': 0.0,
            'total_value_dc': 0.0
        },
        'delivery_metrics': {
            'on_time': 0,
            'delayed': 0,
            'reliability_scores': []
        },
        'territories': {
            'companies': [],
            'sales_orgs': [],
            'plants': [],
            'divisions': []
        },
        'customer_data': {
            'sold_to_parties': [],
            'ship_to_parties': [],
            'countries': [],
            'regions': []
        },
        'orders': []
    }
    
    try:
        # Get user groups for federated search
        user_groups = get_user_groups_from_headers()
        
        # Perform federated search to get full context
        search_results = orchestrate_federated_search("*", user_groups, user_email)
        
        if search_results:
            # Organize results by index
            index_data = {}
            for result in search_results:
                if result.get('_index_name') in Config.SALES_GENERAL_INDEX:
                    # Update metadata with sales data
                    metadata.update(result)
                else:
                    # Store other results in index_data
                    index_name = result.pop('_index_name', 'unknown_index')
                    index_data[index_name] = result
            
            # Store the organized data in metadata
            metadata['index_data'] = index_data
            
            # Store additional search results in metadata for context
            metadata['additional_context'] = []
            for result in search_results[1:3]:  # Store up to 2 additional results
                if result.get('_index_name') not in Config.SALES_GENERAL_INDEX:
                    context_data = {}
                    for key, value in result.items():
                        if key not in ['_federated_score', '_index_name']:
                            context_data[key] = value
                    if context_data:
                        metadata['additional_context'].append(context_data)
            
            # Update cache with new data
            sales_data_cache.set(user_email, metadata)
            
    except Exception as e:
        logger.error(f"Error retrieving sales rep data: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Continue with default metadata
    
    # Filter chat sessions by user_id
    all_chats = ChatSession.query.filter_by(user_id=user_id).order_by(ChatSession.created_at.desc()).all()
    
    # Get current session_id safely
    current_session_id = session.get('session_id')
    
    conversations = [{
        'id': chat.id,
        'title': chat.focus_area or 'New Conversation',
        'time': datetime.datetime.utcnow().fromisoformat(chat.created_at).strftime('%I:%M %p') if chat.created_at else '',
        'active': chat.id == current_session_id
    } for chat in all_chats]

    # Create new session if none exists
    if not current_session_id:
        session['session_id'] = str(uuid4())
        chat_session = initialize_chat_session(session['session_id'], user_id, metadata)
        db.session.add(chat_session)
        safe_commit()
    else:
        # Verify the session exists and belongs to this user
        chat_session = db.session.get(ChatSession, current_session_id)
        if not chat_session or chat_session.user_id != user_id:
            # Invalid or unauthorized session, create new one
            session['session_id'] = str(uuid4())
            chat_session = initialize_chat_session(session['session_id'], user_id, metadata)
            db.session.add(chat_session)
            safe_commit()
        else:
            # Update existing session's metadata with new data
            chat_session.sales_metadata = metadata
            safe_commit()

    # Get current chat session data
    chat_session = db.session.get(ChatSession, session['session_id'])
    
    chat_data = {
        'chat_history': chat_session.chat_history,
        'conversations': conversations,
        'confidence_level': chat_session.confidence_level or 0,
        'product_category': chat_session.product_category or "",
        'detected_language': chat_session.detected_language or "",
        'focus_area': chat_session.focus_area or "New Conversation",
        'metadata': chat_session.sales_metadata
    }

    return render_template("chat.html", user_name=user_email, user_id=user_id, **chat_data)

def log_chat_to_blob(user_id, user_email, chat_input, session_id):
    """
    Log chat messages to Azure Blob Storage in JSONL format.
    
    Args:
        user_id: The ID of the user
        user_email: The email of the user
        chat_input: The message content
        session_id: The chat session ID
    """
    try:
        now = datetime.datetime.utcnow()
        blob_name = f"chats/{now.year}/{now.month:02d}/{now.day:02d}/{now.hour:02d}/chat_log_{int(now.timestamp())}.jsonl"

        # Chat data as JSON
        chat_data = {
            "id": f"chat_{int(now.timestamp())}",
            "timestamp": now.isoformat(),
            "user_id": user_id,
            "user_email": user_email,
            "session_id": session_id,
            "message": chat_input
        }

        # Convert to JSON line
        chat_jsonl = json.dumps(chat_data) + "\n"

        # Initialize the Blob Service Client using DefaultAzureCredential
        account_url = f"https://{Config.AZURE_STORAGE_ACCOUNT}.blob.core.windows.net"
        credential = DefaultAzureCredential()
        blob_service_client = BlobServiceClient(account_url, credential=credential)
        
        # Get the container client for telemetry
        container_client = blob_service_client.get_container_client(Config.AZURE_STORAGE_CONTAINER_TELEMETRY_NAME)

        # Upload to Blob Storage (Append or Create)
        blob_client = container_client.get_blob_client(blob_name)
        try:
            # Try to append to existing blob
            blob_client.append_block(chat_jsonl)
        except ResourceNotFoundError:
            # If blob doesn't exist, create it
            blob_client.upload_blob(chat_jsonl, blob_type="AppendBlob")
        
        logger.info(f"Chat logged to blob: {blob_name}")
        
    except Exception as e:
        logger.error(f"Failed to log chat to blob: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")

@app.route('/message', methods=['POST'])
def handle_message():

    chat_session = db.session.get(ChatSession, session['session_id'])
    
    #check if sales_metadata is empty, if so, set it to an empty dictionary
    try:
        sales_context = chat_session.sales_metadata
    except:
        logger.info("Sales metadata is empty")
        sales_context = {}
    
    # Update last activity timestamp
    chat_session.last_activity = datetime.datetime.utcnow().isoformat()
    
    # Initialize citations list
    citations = []
    
    # Create base messages list with combined system prompt
    combined_system_prompt = get_system_prompt_with_sales_context(get_base_system_prompt(), sales_context)
    
    # Add logging for the combined system prompt
    logger.debug("Combined System Prompt:")
    logger.debug(combined_system_prompt)
    
    new_messages = [{"role": "system", "content": combined_system_prompt}]
    
    # Copy lists for local modifications and limit to last N messages
    new_chat_history = list(chat_session.chat_history[-MESSAGE_HISTORY_LIMIT:])
    
    # For OpenAI messages, use standardized truncation
    conversation_messages = get_truncated_messages(chat_session.messages)
    new_messages.extend(conversation_messages[1:])  # Skip system message as we already added it

    # Add these debug statements
    logger.debug(f"Form data: {request.form}")
    logger.debug(f"Files: {request.files}")
    logger.debug(f"Content Type: {request.content_type}")
    
    # Log incoming request
    logger.debug("Received classification request")
    user_question = request.form.get('question', '').strip()
    uploaded_file = request.files.get('photoupload')
    logger.debug(f"User question: {user_question}")
    logger.debug(f"File uploaded: {bool(uploaded_file and uploaded_file.filename != '')}")

    # Get user information for logging
    user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', Config.DEBUG_USER_ID)
    user_email = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME', Config.DEBUG_USER_EMAIL)
    
    # Log the chat message to blob storage
    log_chat_to_blob(user_id, user_email, user_question, session['session_id'])

    image_data = None
    if uploaded_file and uploaded_file.filename != '':
        try:
            # Check file size
            uploaded_file.seek(0, os.SEEK_END)
            file_size = uploaded_file.tell()
            uploaded_file.seek(0)
            
            if file_size > MAX_FILE_SIZE:
                logger.error(f"File too large: {file_size} bytes")
                return render_template("chat.html", error="File is too large. Please upload a file smaller than 8MB.")
            
            # Process file
            filename = secure_filename(uploaded_file.filename)
            file_mime, _ = mimetypes.guess_type(filename)
            
            if file_mime not in ALLOWED_MIME_TYPES:
                logger.error(f"Unsupported file type: {file_mime}")
                return render_template("chat.html", error="Unsupported file type. Please upload JPEG or PNG files.")
            
            # Read the file and process image
            file_blob = uploaded_file.read()
            image = PILImage.open(BytesIO(file_blob))
            
            # Convert to RGB if necessary (handles both PNG and JPEG)
            if image.mode in ('RGBA', 'P'):
                image = image.convert('RGB')
                
            # Save to buffer
            buffered = BytesIO()
            image.save(buffered, format="JPEG", quality=85)
            image_data = base64.b64encode(buffered.getvalue()).decode('utf-8')
            logger.debug(f"Successfully processed image: {filename}")
            
        except Exception as e:
            logger.error(f"Error processing image: {e}")
            return render_template("chat.html", error="Error processing image. Please try again.")
    
    # Update messages structure for image support
    if image_data:
        # Keep existing messages and append new message with image
        new_messages.append({
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": user_question
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_data}"
                    }
                }
            ]
        })
    else:
        # Regular text message
        new_messages.append({
            "role": "user",
            "content": user_question
        })
    
    # Add before processing assistant response
    new_chat_history.append({
        "role": "user",
        "content": user_question
    })
    
    logger.debug("Sending request to OpenAI")
    logger.debug(f"Number of messages being sent: {len(new_messages)}")
    logger.debug(f"Message roles being sent: {[msg['role'] for msg in new_messages]}")

    # Initialize response variables
    guidance = ""
    confidence_level = 0
    product_category = ""
    focus_area = ""
    detected_language = ""
    actions = None
    
    try:
        client = AzureOpenAI(
            azure_endpoint=Config.AZURE_OPENAI_ENDPOINT,
            api_key=Config.AZURE_OPENAI_KEY,
            api_version=Config.AZURE_OPENAI_API_VERSION,
        )

        # Determine which index to use based on the request path
        referrer = request.referrer or ""
        if "/food" in referrer:
            # For food route, don't include data source
            response = client.chat.completions.create(
                model=Config.AZURE_OPENAI_DEPLOYMENT,
                messages=new_messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS['COMPLETION'],
                stream=False
            )
        else:
            # For default route, include data source with semantic search
            index_name = Config.AZURE_OPENAI_SEARCH_INDEX
            semantic_config = f"{Config.AZURE_OPENAI_SEARCH_INDEX}-semantic-configuration"
            query_type = "semantic"
            data_source = {
                "type": "azure_search",
                "parameters": {
                    "endpoint": Config.AZURE_AI_SEARCH_ENDPOINT,
                    "key": Config.AZURE_AI_SEARCH_KEY,
                    "index_name": index_name,
                    "semantic_configuration": semantic_config,
                    "query_type": query_type,
                    "fields_mapping": {},
                    "in_scope": True,
                    "filter": None,
                    "strictness": 4,
                    "top_n_documents": Config.AZURE_AI_SEARCH_TOP_N_DOCS,
                    "authentication": {
                        "type": "api_key",
                        "key": Config.AZURE_AI_SEARCH_KEY
                    }
                }
            }
            response = client.chat.completions.create(
                model=Config.AZURE_OPENAI_DEPLOYMENT,
                messages=new_messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS['COMPLETION'],
                stream=False,
                extra_body={
                    "data_sources": [data_source]
                }
            )
    
        logger.debug("Successfully received Azure OpenAI response")
        
        # Extract citations and context information
        if hasattr(response.choices[0].message, 'context'):
            context = response.choices[0].message.context
            
            # Handle citations
            if context and 'citations' in context:
                citations = [{
                    'title': citation.get('title', ''),
                    'content': citation.get('content', ''),  # New field in the schema
                    'filepath': citation.get('filepath', ''),
                    'url': (f"/documents/{citation.get('filepath')}" if citation.get('filepath')
                            else f"/documents/{citation.get('url').split('/')[-1]}" if citation.get('url')
                            else ""),  # Provide default empty string if both are None
                    'chunk_id': citation.get('chunk_id', '')  # New field in the schema
                } for citation in context['citations']]
                
                # Log retrieved documents if available
                if 'all_retrieved_documents' in context:
                    logger.debug("All retrieved documents:")
                    for doc in context['all_retrieved_documents']:
                        logger.debug(f"  Search queries: {doc.get('search_queries', [])}")
                        logger.debug(f"  Data source index: {doc.get('data_source_index')}")
                        logger.debug(f"  Original search score: {doc.get('original_search_score')}")
                        logger.debug(f"  Rerank score: {doc.get('rerank_score')}")
                        logger.debug(f"  Filter reason: {doc.get('filter_reason')}")
                
                # Log intent if available
                if 'intent' in context:
                    logger.debug(f"Detected intent: {context['intent']}")

        guidance = response.choices[0].message.content.strip()
        
        # Clean up guidance text
        guidance = guidance.replace('```', '')
        guidance = guidance.strip()
        
        # Filter out response prefixes using regex
        prefix_pattern = r'^\s*(?:#+\s*)?(?:PART\s*2\s*[-:]*\s*(?:RESPONSE)?[^\n]*\n)'
        guidance = re.sub(prefix_pattern, '', guidance, flags=re.IGNORECASE|re.MULTILINE).strip()

        try:
            # Now try to parse the JSON metadata
            json_str = guidance[guidance.find('{'):guidance.find('}')+1]
            conversation = guidance[guidance.find('}')+1:]
            
            # Parse the JSON metadata
            metadata = json.loads(json_str)
            
            # Clean up the conversation text - only trim start and end
            guidance = conversation.strip()

            # Store metadata values with new fields
            confidence_level = metadata.get('confidence_level', 0)
            product_category = metadata.get('product_category')
            focus_area = metadata.get('query_focus_area')
            detected_language = metadata.get('detected_language')
            key_takeaways = metadata.get('key_takeaways', [])
                
            # Process actions field
            actions = metadata.get('actions', None)
            if actions == "":
                actions = None
                
            if actions == "send_email":
                try:
                    # Generate final approved email draft
                    email_package = email_service.generate_email_content(chat_session, citations)
                    
                    # Get access token from headers
                    access_token = request.headers.get('X-MS-TOKEN-AAD-ACCESS-TOKEN')
                    
                    # Use synchronous version instead of async
                    draft_result = email_service.create_email_draft_sync(email_package, user_email, access_token)
                    
                    if draft_result["success"]:
                        guidance += "\n\nEmail Draft Created:\n"
                        guidance += f"\nSubject: {email_package['subject']}"
                        guidance += f"\n\n{email_package['body']}"
                        if email_package['attachments']:
                            guidance += "\n\nAttachments:"
                            for att in email_package['attachments']:
                                guidance += f"\n- {att['title']}"
                        
                        guidance += "\n\nThe email draft has been automatically created in your Outlook drafts folder."
                        guidance += "\nYou can review and send it from your email client."
                    else:
                        guidance += "\n\nI created the email draft but couldn't save it to your drafts folder."
                        guidance += "\nYou can copy the content above and create the email manually."
                        logger.error(f"\nError: " + draft_result.get("error", "Unknown error") ) 
                
                except Exception as e:
                    logger.error(f"Failed to process email action: {e}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    guidance += "\n\nI encountered an error while creating the email draft. Please try again."
                    actions = None

        except (ValueError, json.JSONDecodeError) as e:
            logger.debug(f"No valid JSON metadata found in response: {e}")
            # If JSON parsing fails, use the entire cleaned guidance as the response
            confidence_level = 0
            product_category = ""
            focus_area = ""
            detected_language = ""
            key_takeaways = []
            actions = None # Set actions to None if JSON parsing fails

        # Update session metadata
        chat_session.confidence_level = confidence_level
        chat_session.product_category = product_category
        chat_session.focus_area = focus_area
        chat_session.detected_language = detected_language
        
        # Update chat history with cleaned text and metadata
        new_chat_history.append({
            "role": "assistant", 
            "content": guidance,
            "citations": citations if actions is None else [],  # More explicit check for None
            "metadata": {
                "key_takeaways": key_takeaways,
                "actions": actions  # Include the actions in metadata
            }
        })
        new_messages.append({"role": "assistant", "content": guidance})
        chat_session.chat_history = new_chat_history
        chat_session.messages = new_messages

        # After processing citations
        chat_session.citations = citations if actions is None else []  # More explicit check for None
        
        # Add token monitoring
        logger.debug("Token Usage Analysis:")
        logger.debug(f"  Prompt tokens used: {response.usage.prompt_tokens}/{MAX_TOKENS['PROMPT']}")
        logger.debug(f"  Completion tokens used: {response.usage.completion_tokens}/{MAX_TOKENS['COMPLETION']}")
        logger.debug(f"  Total tokens used: {response.usage.total_tokens}/{MAX_TOKENS['TOTAL']}")
        logger.debug(f"  Prompt tokens percentage: {(response.usage.prompt_tokens/MAX_TOKENS['PROMPT'])*100:.1f}%")

    except Exception as e:
        logger.error(f"Error processing response: {e}")
        guidance = "Sorry, I encountered an error processing your request."
        new_chat_history.append({
            "role": "assistant", 
            "content": guidance,
            "citations": []  # No citations when there's an error
        })
        chat_session.chat_history = new_chat_history

    # Store sales metadata before committing
    sales_metadata = chat_session.sales_metadata

    # After modifications, save to database:
    safe_commit()
    
    # Refresh the session to ensure we have the latest data
    db.session.refresh(chat_session)

    # Return JSON response with citations
    return jsonify({
        'response': guidance,
        'metadata': {
            'confidence_level': confidence_level,
            'product_category': product_category,
            'focus_area': focus_area,
            'detected_language': detected_language,
            'metadata': sales_metadata  # Include sales metadata in the response
        },
        'citations': citations if actions is None else []  # More explicit check for None
    })


chat_creation_lock = Lock()

def cleanup_old_empty_chats():
    """Remove empty chats that are older than the timeout period"""
    try:
        current_time = datetime.datetime.utcnow()
        cutoff_time = (current_time - datetime.timedelta(seconds=EMPTY_CHAT_TIMEOUT)).isoformat()
        
        # Find all empty chats older than the cutoff
        old_empty_chats = ChatSession.query.filter(
            ChatSession.chat_history == [],  # Empty chat history
            ChatSession.created_at < cutoff_time  # Older than timeout
        ).all()
        
        # Delete the old empty chats
        for chat in old_empty_chats:
            db.session.delete(chat)
        
        safe_commit()
        logger.info(f"Cleaned up {len(old_empty_chats)} old empty chats")
        
    except Exception as e:
        logger.error(f"Failed to cleanup old empty chats: {str(e)}")
        db.session.rollback()

@app.route('/new_chat', methods=['POST'])
def new_chat():
    with chat_creation_lock:
        if session.get('creating_chat'):
            return jsonify({"success": False, "error": "Chat creation in progress"})
        session['creating_chat'] = True
        try:
            # Clean up old empty chats first
            cleanup_old_empty_chats()
            
            # Get and validate user ID first
            user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID')
            user_email = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME')
            
            # Add local development bypass using environment variables
            if Config.IS_LOCAL_DEV:
                user_id = user_id or Config.get_env('DEBUG_USER_ID', 'local-dev-user')
                user_email = user_email or Config.get_env('DEBUG_USER_EMAIL', 'local-dev@example.com')
            
            if not user_id:
                return jsonify({"success": False, "error": "Authentication required"}), 401

            # Check if user has any empty chats that are recent
            current_time = datetime.datetime.utcnow()
            user_chats = ChatSession.query.filter_by(user_id=user_id).all()
            for chat in user_chats:
                if len(chat.chat_history) == 0:
                    # Check if chat is within the empty chat timeout period
                    chat_created = datetime.datetime.fromisoformat(chat.created_at)
                    if (current_time - chat_created).total_seconds() < EMPTY_CHAT_TIMEOUT:
                        return jsonify({
                            "success": False,
                            "error": "You already have an empty chat. Please use your existing empty chat before creating a new one."
                        })

            # Initialize metadata with defaults
            metadata = {
                'Email': user_email if user_email else "Not Available",
                'Phone': "Not Available",
                'total_orders': 0,
                'blocked_orders': 0,
                'total_order_quantity': 0,
                'total_open_quantity': 0,
                'execution_status': [],
                'stock_availability': {
                    'claimed': 0,
                    'not_claimed': 0
                },
                'sales_documents': {
                    'types': [],
                    'total_value_usd': 0.0,
                    'total_value_dc': 0.0
                },
                'delivery_metrics': {
                    'on_time': 0,
                    'delayed': 0,
                    'reliability_scores': []
                },
                'territories': {
                    'companies': [],
                    'sales_orgs': [],
                    'plants': [],
                    'divisions': []
                },
                'customer_data': {
                    'sold_to_parties': [],
                    'ship_to_parties': [],
                    'countries': [],
                    'regions': []
                },
                'orders': []
            }
            
            # Try to find sales rep data if we have an email
            if user_email:
                try:
                    sales_rep = get_cached_sales_rep_data(user_email)
                    if sales_rep:
                        # Since sales_rep already has the correct structure from orchestrator,
                        # we can directly update the metadata with it
                        metadata.update(sales_rep)
                except Exception as e:
                    logger.error(f"Error retrieving cached sales rep data: {e}")
                    # Continue with default metadata
            
            session['session_id'] = str(uuid4())
            chat_session = initialize_chat_session(session['session_id'], user_id, metadata)
            db.session.add(chat_session)
            safe_commit()
            
            return jsonify({"success": True})
        
        except Exception as e:
            logger.error(f"Error creating new chat: {str(e)}")
            return jsonify({"success": False, "error": str(e)})
        
        finally:
            session.pop('creating_chat', None)  # Always clean up the lock

@app.route('/switch_chat', methods=['POST'])
def switch_chat():
    data = request.get_json()
    new_session_id = data.get('session_id')
    
    # Verify session exists
    chat_session = db.session.get(ChatSession, new_session_id)
    if chat_session:
        # Update session ID
        session['session_id'] = new_session_id
        
        # Get user email from headers
        user_email = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME')
        
        # Initialize default metadata
        metadata = {
            'Email': user_email if user_email else "Not Available",
            'Phone': "Not Available",
            'total_orders': 0,
            'blocked_orders': 0,
            'total_order_quantity': 0,
            'total_open_quantity': 0,
            'execution_status': [],
            'stock_availability': {
                'claimed': 0,
                'not_claimed': 0
            },
            'sales_documents': {
                'types': [],
                'total_value_usd': 0.0,
                'total_value_dc': 0.0
            },
            'delivery_metrics': {
                'on_time': 0,
                'delayed': 0,
                'reliability_scores': []
            },
            'territories': {
                'companies': [],
                'sales_orgs': [],
                'plants': [],
                'divisions': []
            },
            'customer_data': {
                'sold_to_parties': [],
                'ship_to_parties': [],
                'countries': [],
                'regions': []
            },
            'orders': []
        }
        
        # If chat session has metadata, use it, otherwise use defaults
        session_metadata = chat_session.sales_metadata
        if session_metadata and has_sales_rep_data(session_metadata):
            metadata = session_metadata
        
        # Return the metadata
        return jsonify({
            "success": True,
            "metadata": metadata
        })
    return jsonify({"success": False, "error": "Session not found"}), 404

@app.route('/documents/<path:filename>')
def serve_document(filename):
    try:
        # URL decode the filename first
        decoded_filename = urllib.parse.unquote(filename)
        logger.debug(f"Original filename: {filename}")
        logger.debug(f"Decoded filename: {decoded_filename}")
        
        # Check for security issues after decoding
        if not decoded_filename or \
           '..' in decoded_filename or \
           decoded_filename.startswith('.') or \
           len(decoded_filename) > 255 or \
           any(c in decoded_filename for c in '<>:"|?*'):
            logger.error(f"Invalid filename format: {decoded_filename}")
            return "Invalid filename", 400
            
        # Initialize the Blob Service Client using DefaultAzureCredential
        account_url = f"https://{Config.AZURE_STORAGE_ACCOUNT}.blob.core.windows.net"
        credential = DefaultAzureCredential()
        blob_service_client = BlobServiceClient(account_url, credential=credential)
        
        # Get the container client
        container_name = Config.AZURE_STORAGE_CONTAINER_NAME
        container_client = blob_service_client.get_container_client(container_name)
        
        # List all blobs (for debugging)
        try:
            blobs = list(container_client.list_blobs(name_starts_with=decoded_filename.split('(')[0].strip()))
            logger.debug(f"Found {len(blobs)} matching blobs:")
            for blob in blobs:
                logger.debug(f"  - {blob.name}")
        except Exception as e:
            logger.error(f"Failed to list blobs: {str(e)}")
        
        # Try to get the blob with exact filename
        try:
            logger.debug(f"Attempting to get blob: {decoded_filename}")
            blob_client = container_client.get_blob_client(decoded_filename)
            blob_properties = blob_client.get_blob_properties()
            blob_data = blob_client.download_blob()
            logger.debug(f"Successfully accessed blob: {decoded_filename}")
        except ResourceNotFoundError:
            # If not found, try without (1)
            alt_filename = decoded_filename.replace(' (1)', '')
            logger.debug(f"File not found, trying alternative: {alt_filename}")
            try:
                blob_client = container_client.get_blob_client(alt_filename)
                blob_properties = blob_client.get_blob_properties()
                blob_data = blob_client.download_blob()
                logger.debug(f"Successfully accessed alternative blob: {alt_filename}")
            except ResourceNotFoundError as e:
                logger.error(f"Neither version of file found: {decoded_filename} or {alt_filename}")
                return f"Document not found: {decoded_filename}", 404
            except Exception as e:
                logger.error(f"Error accessing alternative file: {str(e)}")
                raise
        
        # Get content type
        content_type = blob_properties.content_settings.content_type
        if not content_type:
            content_type, _ = mimetypes.guess_type(decoded_filename)
            content_type = content_type or 'application/octet-stream'
            
        # Ensure PDFs have the correct content type
        if decoded_filename.lower().endswith('.pdf'):
            content_type = 'application/pdf'
        
        logger.debug(f"Serving file with content type: {content_type}")
        
        # Stream the file
        return blob_data.readall(), 200, {
            'Content-Type': content_type,
            'Content-Disposition': f'inline; filename="{decoded_filename}"',
            'Cache-Control': 'no-cache'  # Prevent caching issues
        }
        
    except Exception as e:
        logger.error(f"Unhandled error serving document: {str(e)}")
        logger.error(f"Error type: {type(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return f"Error accessing document: {str(e)}", 500

def safe_commit():
    try:
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error: {str(e)}")
        raise

# Helper functions for consistent system prompt handling
def get_base_system_prompt():
    """Get the standardized base system prompt."""
    SYSTEM_PROMPT = ""
    #if referer is food, use food system prompt, else use protective system prompt
    if request.referrer and 'food' in request.referrer.lower():
        logger.debug("Using food system prompt")
        SYSTEM_PROMPT = FOOD_SYSTEM_PROMPT.strip()
    else:
        logger.debug("Using protective system prompt")
        SYSTEM_PROMPT = PROTECTIVE_SYSTEM_PROMPT.strip()

    return validate_system_prompt(SYSTEM_PROMPT)

def validate_system_prompt(prompt):
    """Validate system prompt structure and content."""
    if not prompt or not isinstance(prompt, str):
        raise ValueError("Invalid system prompt")
    if "[SALES REP CONTEXT HERE]" not in prompt:
        logger.warning("System prompt missing sales context placeholder. There will be no sales context in the response.")
    return prompt.strip()

def get_truncated_messages(messages, limit=MESSAGE_HISTORY_LIMIT):
    """Standardized message history truncation that preserves system prompt."""
    if not messages or not isinstance(messages, list):
        logger.warning("Empty or invalid messages list, returning empty list")
        return []
        
    try:
        # Always keep system message if it exists
        if len(messages) > 0 and messages[0].get('role') == 'system':
            system_message = messages[0]
            user_messages = messages[1:]
        else:
            logger.warning("No system message found at start of messages")
            system_message = {"role": "system", "content": get_base_system_prompt()}
            user_messages = messages
            
        # Validate message structure
        for msg in user_messages:
            if not isinstance(msg, dict) or 'role' not in msg or 'content' not in msg:
                logger.warning(f"Invalid message structure found: {msg}")
                continue
                
        # Truncate user messages
        truncated_messages = user_messages[-limit:] if len(user_messages) > limit else user_messages
        
        # Return combined messages
        return [system_message] + truncated_messages
    except Exception as e:
        logger.error(f"Error in message truncation: {str(e)}")
        # Return at least a system message in case of error
        return [{"role": "system", "content": get_base_system_prompt()}]

def initialize_chat_session(session_id, user_id, metadata):
    """Standardized chat session initialization."""
    system_prompt = get_system_prompt_with_sales_context(get_base_system_prompt(), metadata)
    return ChatSession(
        id=session_id,
        messages=[{"role": "system", "content": system_prompt}],
        chat_history=[],
        detected_language="",
        focus_area="New Conversation",
        created_at=datetime.datetime.utcnow().isoformat(),
        last_activity=datetime.datetime.utcnow().isoformat(),
        user_id=user_id,
        sales_metadata=metadata,
        product_category="",  # Add missing field
        confidence_level=0,   # Add missing field
        citations=[]          # Add missing field
    )


def log_feedback_to_blob(user_id, user_email, feedback_type, feedback_content, session_id, metadata=None):
    """
    Log user feedback to Azure Blob Storage in JSONL format.
    
    Args:
        user_id: The ID of the user
        user_email: The email of the user
        feedback_type: Type of feedback ('problem' or 'idea')
        feedback_content: The feedback message
        session_id: The chat session ID
        metadata: Additional metadata to include (optional)
    """
    try:
        now = datetime.datetime.utcnow()
        blob_name = f"feedback/{now.year}/{now.month:02d}/{now.day:02d}/feedback_log_{int(now.timestamp())}.jsonl"

        # Feedback data as JSON
        feedback_data = {
            "id": f"feedback_{int(now.timestamp())}",
            "timestamp": now.isoformat(),
            "user_id": user_id,
            "user_email": user_email,
            "session_id": session_id,
            "feedback_type": feedback_type,
            "content": feedback_content,
            "metadata": metadata or {}
        }

        # Convert to JSON line
        feedback_jsonl = json.dumps(feedback_data) + "\n"

        # Initialize the Blob Service Client using DefaultAzureCredential
        account_url = f"https://{Config.AZURE_STORAGE_ACCOUNT}.blob.core.windows.net"
        credential = DefaultAzureCredential()
        blob_service_client = BlobServiceClient(account_url, credential=credential)

        # Get the container client for feedback
        container_client = blob_service_client.get_container_client(Config.AZURE_STORAGE_CONTAINER_FEEDBACK_NAME)

        # Upload to Blob Storage (Append or Create)
        blob_client = container_client.get_blob_client(blob_name)
        try:
            # Try to append to existing blob
            blob_client.append_block(feedback_jsonl)
        except ResourceNotFoundError:
            # If blob doesn't exist, create it
            blob_client.upload_blob(feedback_jsonl, blob_type="AppendBlob")

        logger.info(f"Feedback logged to blob: {blob_name}")
        return True

    except Exception as e:
        logger.error(f"Failed to log feedback to blob: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return False

# Add input validation functions
def sanitize_feedback_content(content: str) -> str:
    """
    Sanitize feedback content by:
    1. Removing HTML tags
    2. Converting HTML entities
    3. Removing control characters
    4. Limiting length
    5. Trimming whitespace
    
    Args:
        content: Raw feedback content
    Returns:
        Sanitized content string
    """
    if not content:
        return ""
        
    # Convert HTML entities and remove HTML tags
    content = html.escape(content)
    content = re.sub(r'<[^>]*>', '', content)
    
    # Remove control characters except newlines and tabs
    content = ''.join(char for char in content if char == '\n' or char == '\t' or (32 <= ord(char) <= 126))
    
    # Limit length (e.g., 2000 characters)
    content = content[:2000]
    
    # Normalize whitespace
    content = ' '.join(content.split())
    
    return content.strip()

def validate_feedback_input(data: dict) -> Tuple[bool, Optional[str], Optional[dict]]:
    """
    Validate and sanitize feedback input data.
    
    Args:
        data: Raw feedback data dictionary
    Returns:
        Tuple of (is_valid, error_message, sanitized_data)
    """
    if not isinstance(data, dict):
        return False, "Invalid request format", None
        
    feedback_type = data.get('type', '').lower().strip()
    feedback_content = data.get('content', '')
    
    # Validate feedback type
    if not feedback_type:
        return False, "Feedback type is required", None
    if feedback_type not in ['problem', 'idea']:
        return False, "Invalid feedback type", None
        
    # Validate content
    if not feedback_content:
        return False, "Feedback content is required", None
    
    # Sanitize content
    sanitized_content = sanitize_feedback_content(feedback_content)
    if not sanitized_content:
        return False, "Invalid feedback content", None
    
    # Return sanitized data
    return True, None, {
        'type': feedback_type,
        'content': sanitized_content
    }

@app.route('/feedback', methods=['POST'])
def handle_feedback():
    """Handle user feedback submission with input sanitization."""
    try:
        # Get user information
        user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID', Config.DEBUG_USER_ID)
        user_email = request.headers.get('X-MS-CLIENT-PRINCIPAL-NAME', Config.DEBUG_USER_EMAIL)
        
        # Validate user authentication
        if not user_id or not user_email:
            return jsonify({
                "success": False,
                "error": "Authentication required"
            }), 401
        
        # Get and validate feedback data
        try:
            data = request.get_json()
        except Exception as e:
            return jsonify({
                "success": False,
                "error": "Invalid JSON data"
            }), 400
            
        # Validate and sanitize input
        is_valid, error_message, sanitized_data = validate_feedback_input(data)
        if not is_valid:
            return jsonify({
                "success": False,
                "error": error_message
            }), 400
            
        session_id = session.get('session_id')
        if not session_id:
            return jsonify({
                "success": False,
                "error": "No active session"
            }), 400

        # Get current chat session for metadata
        chat_session = db.session.get(ChatSession, session_id)
        metadata = {
            "product_category": chat_session.product_category if chat_session else "",
            "focus_area": chat_session.focus_area if chat_session else "",
            "detected_language": chat_session.detected_language if chat_session else "",
            "sales_metadata": chat_session.sales_metadata if chat_session else {}
        }

        # Log the feedback with sanitized data
        success = log_feedback_to_blob(
            user_id=user_id,
            user_email=user_email,
            feedback_type=sanitized_data['type'],
            feedback_content=sanitized_data['content'],
            session_id=session_id,
            metadata=metadata
        )

        if success:
            return jsonify({"success": True})
        else:
            return jsonify({
                "success": False,
                "error": "Failed to save feedback"
            }), 500

    except Exception as e:
        logger.error(f"Error handling feedback: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            "success": False,
            "error": "Internal server error"
        }), 500

def get_user_groups_from_headers() -> list:
    """
    Retrieve user group memberships from request headers.
    Supports both JWT token-based groups and header-based groups.
    """
    groups = []
    
    try:
        # Try to get groups from token
        user_id_token = request.headers.get("X-MS-TOKEN-AAD-ID-TOKEN", "")
        if user_id_token:
            try:
                decoded = jwt.decode(user_id_token, options={"verify_signature": False})
                groups.extend(decoded.get("groups", []))
            except PyJWTError as e:
                logger.warning(f"Failed to decode JWT token: {e}")
        
        # Try to get groups from X-MS-CLIENT-GROUPS header (comma-separated)
        group_header = request.headers.get("X-MS-CLIENT-GROUPS", "")
        if group_header:
            header_groups = [g.strip() for g in group_header.split(",") if g.strip()]
            groups.extend(header_groups)
            
        # Deduplicate groups
        groups = list(set(groups))
    
    except Exception as e:
        logger.error(f"Error getting user groups: {e}")
        groups = []
    
    # For local development/debugging
    if Config.IS_LOCAL_DEV and not groups:
        try:
            debug_groups = json.loads(Config.DEBUG_USER_GROUPS)
            if isinstance(debug_groups, list):
                groups = debug_groups
        except json.JSONDecodeError:
            groups = ["SalesTeam"]  # Default fallback
    
    logger.debug(f"User groups: {groups}")
    return groups

def get_allowed_indexes(user_groups: list) -> set:
    """
    Get the set of allowed indexes based on user group membership.
    
    Args:
        user_groups: List of group names the user belongs to
        
    Returns:
        Set of index names the user has access to
    """
    allowed_indexes = set()
    
    # Add indexes for each group the user belongs to
    for group in user_groups:
        if group in Config.GROUP_TO_INDEX_MAP:
            allowed_indexes.update(Config.GROUP_TO_INDEX_MAP[group])
    
    logger.debug(f"Allowed indexes for user: {allowed_indexes}")
    return allowed_indexes

def orchestrate_federated_search(query: str, user_groups: list, email: str) -> list:
    """
    Perform federated search across all indexes the user has access to and process sales data.
    """
    allowed_indexes = get_allowed_indexes(user_groups)
    
    if not allowed_indexes:
        logger.info("User has no accessible indexes")
        return []
    
    all_results = []
    aggregated_data = {
        'execution_status': set(),
        'blocked_orders': 0,
        'total_orders': 0,
        'total_order_quantity': 0,
        'total_open_quantity': 0,
        'stock_availability': {
            'claimed': 0,
            'not_claimed': 0
        },
        'sales_documents': {
            'types': set(),
            'total_value_usd': 0.0,
            'total_value_dc': 0.0
        },
        'delivery_metrics': {
            'on_time': 0,
            'delayed': 0,
            'reliability_scores': set()
        },
        'territories': {
            'companies': set(),
            'sales_orgs': set(),
            'plants': set(),
            'divisions': set()
        },
        'customer_data': {
            'sold_to_parties': set(),
            'ship_to_parties': set(),
            'countries': set(),
            'regions': set()
        },
        'orders': []
    }
    
    for index_name in allowed_indexes:
        try:
            # Specify only the fields we want to return
            search_query = {
                "$search": query if query else "*",
                "$filter": f"Email_ID eq '{email}'",
                "$select": "Sales_Order_Number,Sales_Order_Line_Execution_Status,Customer_Classification,Blocked_Header,Sales_Order_Schedule_line_status,Order_Quantity,Open_Quantity,Stock_Availability_Claimed,Credit_hold_date_Start,Credit_Hold_Date_removal,Requested_Delivery_Date_1,Customer,Division,Sales_Document_Type,Company_Code,Sales_Organization,Payment_Terms,Delivery_Number,Delivery_Created_on,Created_By,Plant,Final_Shipment_Date,Committed_Delivery_Date,Committed_Goods_Issue_Date,Base_Unit_of_Measure,Requested_Delivery_Date,Requested_Goods_Issue_Date,Document_Currency,Customer_Purchase_Order_Date,Customer_PO,Sales_Employee,Credit_Status,Rejection_Status,Sales_Order_Item_Value,Confirmed_Delivery_Date,Credit_Representative,Planned_Delivery_Time_in_Days,Cummulative_Confirmed_Quantity,Delivery_Reliability,Order_Due_Date,Sales_District,Claimed_Stock_Quantity,Issuing_Plant,Ship_to_Customer,Quantity_Closed,Customer_Service_Representative,Sales_Value_Document_Currency,Shipment_Number,Incoterms,Created_On,Open_Sales_Value,Customer_Purchase_Order_Type_Itm_VBKD_BSARK,Sales_Value_in_USD,Profit_Center,Material,Product_Hierarchy,Overall_Processing_Status,Delivery_Status,Ship_to_Region,Ship_to_Country,Ship_to_Country_State,Sold_to_Region_State,Reference_Line,Reference_Order,Total_Sales_Order_Value,Overall_Processing_Status_Text_Hdr_VBUK_GBSTK,Sales_Emp_Key,GID,Email_ID"
            }
            
            headers = {
                'Content-Type': 'application/json',
                'api-key': Config.AZURE_AI_SEARCH_KEY
            }

            url = f"{Config.AZURE_AI_SEARCH_ENDPOINT}/indexes/{index_name}/docs?api-version=2023-11-01"
             
            logger.debug(f"Searching index {index_name}")
            logger.debug(f"Search query: {json.dumps(search_query, indent=2)}")

            for attempt in range(3):
                try:
                    response = requests.get(
                        url,
                        headers=headers,
                        params=search_query,
                        timeout=30  # Add timeout
                    )
                    response.raise_for_status()
                    break
                except requests.exceptions.RequestException as e:
                    if attempt == 2:  # Last attempt
                        raise
                    time.sleep(2 ** attempt)  # Exponential backoff
            
            
            if response.status_code == 200:
                results = response.json().get('value', [])
                logger.debug(f"{index_name} found {len(results)} results")
                
                # Process sales data if this is a sales index
                if index_name in Config.SALES_GENERAL_INDEX:
                    for row in results:
                        # Aggregate sales data
                        aggregated_data['total_orders'] += 1
                        
                        # Process execution status
                        if row.get('Sales_Order_Line_Execution_Status'):
                            aggregated_data['execution_status'].add(row.get('Sales_Order_Line_Execution_Status'))
                        
                        # Process blocked orders
                        if row.get('Blocked_Header') == 'Y':
                            aggregated_data['blocked_orders'] += 1
                        
                        # Process quantities
                        try:
                            if row.get('Order_Quantity'):
                                aggregated_data['total_order_quantity'] += float(row.get('Order_Quantity'))
                            if row.get('Open_Quantity'):
                                aggregated_data['total_open_quantity'] += float(row.get('Open_Quantity'))
                        except (ValueError, TypeError):
                            pass
                        
                        # Track stock availability
                        if row.get('Stock_Availability_Claimed') == '1.000000':
                            aggregated_data['stock_availability']['claimed'] += 1
                        else:
                            aggregated_data['stock_availability']['not_claimed'] += 1
                        
                        # Add territory information
                        if row.get('Company_Code'):
                            aggregated_data['territories']['companies'].add(row.get('Company_Code'))
                        if row.get('Sales_Organization'):
                            aggregated_data['territories']['sales_orgs'].add(row.get('Sales_Organization'))
                        if row.get('Plant'):
                            aggregated_data['territories']['plants'].add(row.get('Plant'))
                        if row.get('Division'):
                            aggregated_data['territories']['divisions'].add(row.get('Division'))
                        
                        # Add customer information
                        if row.get('Customer'):
                            aggregated_data['customer_data']['sold_to_parties'].add(row.get('Customer'))
                        if row.get('Ship_to_Customer'):
                            aggregated_data['customer_data']['ship_to_parties'].add(row.get('Ship_to_Customer'))
                        if row.get('Ship_to_Country'):
                            aggregated_data['customer_data']['countries'].add(row.get('Ship_to_Country'))
                        if row.get('Ship_to_Region'):
                            aggregated_data['customer_data']['regions'].add(row.get('Ship_to_Region'))
                        
                        # Add sales document information
                        if row.get('Sales_Document_Type'):
                            aggregated_data['sales_documents']['types'].add(row.get('Sales_Document_Type'))
                        try:
                            if row.get('Sales_Value_in_USD'):
                                aggregated_data['sales_documents']['total_value_usd'] += float(row.get('Sales_Value_in_USD'))
                            if row.get('Sales_Value_Document_Currency'):
                                aggregated_data['sales_documents']['total_value_dc'] += float(row.get('Sales_Value_Document_Currency'))
                        except (ValueError, TypeError):
                            pass
                        
                        # Track delivery reliability
                        if row.get('Delivery_Reliability'):
                            aggregated_data['delivery_metrics']['reliability_scores'].add(row.get('Delivery_Reliability'))
        
                        # Add order detail with all available fields
                        order_detail = {
                            'order_number': row.get('Sales_Order_Number', 'N/A'),
                            'execution_status': row.get('Sales_Order_Line_Execution_Status', 'Unknown'),
                            'customer_classification': row.get('Customer_Classification', 'Unknown'),
                            'blocked_header': row.get('Blocked_Header', 'N/A'),
                            'order_quantity': row.get('Order_Quantity'),
                            'open_quantity': row.get('Open_Quantity'),
                            'stock_claimed': row.get('Stock_Availability_Claimed'),
                            'delivery_number': row.get('Delivery_Number', 'N/A'),
                            'delivery_created_on': row.get('Delivery_Created_on'),
                            'sales_doc_type': row.get('Sales_Document_Type'),
                            'company_code': row.get('Company_Code'),
                            'sales_org': row.get('Sales_Organization'),
                            'order_status': row.get('Sales_Order_Schedule_line_status', 'N/A'),
                            'value_usd': float(row.get('Sales_Value_in_USD', 0)),
                            'value_dc': row.get('Sales_Value_Document_Currency'),
                            'delivery_reliability': row.get('Delivery_Reliability'),
                            'credit_status': {
                                'overall_status': row.get('Credit_Status', ''),
                                'hold_date_start': row.get('Credit_hold_date_Start'),
                                'last_hold_removed': row.get('Credit_Hold_Date_removal')
                            },
                            'customer_info': {
                                'sold_to': row.get('Customer', 'Unknown'),
                                'ship_to': row.get('Ship_to_Customer', 'Unknown'),
                                'ship_to_country': row.get('Ship_to_Country', 'Unknown'),
                                'ship_to_region': row.get('Ship_to_Region', 'Unknown'),
                                'ship_to_state': row.get('Ship_to_Country_State', 'Unknown'),
                                'sold_to_region_state': row.get('Sold_to_Region_State', 'Unknown'),
                                'purchase_order': row.get('Customer_PO', 'N/A'),
                                'po_date': row.get('Customer_Purchase_Order_Date', 'N/A'),
                                'po_type': row.get('Customer_Purchase_Order_Type_Itm_VBKD_BSARK', 'N/A')
                            },
                            'delivery_info': {
                                'committed_delivery_date': row.get('Committed_Delivery_Date'),
                                'committed_gi_date': row.get('Committed_Goods_Issue_Date'),
                                'requested_delivery_date': row.get('Requested_Delivery_Date'),
                                'requested_gi_date': row.get('Requested_Goods_Issue_Date'),
                                'confirmed_delivery_date': row.get('Confirmed_Delivery_Date'),
                                'final_shipment_date': row.get('Final_Shipment_Date'),
                                'planned_delivery_time_days': row.get('Planned_Delivery_Time_in_Days'),
                                'base_uom': row.get('Base_Unit_of_Measure'),
                                'order_due_date': row.get('Order_Due_Date'),
                                'delivery_number': row.get('Delivery_Number'),
                                'shipment_number': row.get('Shipment_Number')
                            },
                            'product_info': {
                                'material': row.get('Material'),
                                'product_hierarchy': row.get('Product_Hierarchy'),
                                'division': row.get('Division'),
                                'profit_center': row.get('Profit_Center'),
                                'plant': row.get('Plant'),
                                'issuing_plant': row.get('Issuing_Plant'),
                                'claimed_stock_quantity': row.get('Claimed_Stock_Quantity')
                            },
                            'status_info': {
                                'overall_status': row.get('Overall_Processing_Status', 'N/A'),
                                'overall_status_text': row.get('Overall_Processing_Status_Text_Hdr_VBUK_GBSTK', 'N/A'),
                                'delivery_status': row.get('Delivery_Status', 'N/A'),
                                'rejection_status': row.get('Rejection_Status', 'N/A')
                            },
                            'sales_team': {
                                'sales_employee': row.get('Sales_Employee', 'N/A'),
                                'sales_emp_key': row.get('Sales_Emp_Key', 'N/A'),
                                'credit_rep': row.get('Credit_Representative', 'N/A'),
                                'customer_service_representative': row.get('Customer_Service_Representative', 'N/A'),
                                'created_by': row.get('Created_By', 'N/A'),
                                'sales_district': row.get('Sales_District', 'N/A'),
                                'gid': row.get('GID', 'N/A')
                            },
                            'additional_info': {
                                'payment_terms': row.get('Payment_Terms', 'N/A'),
                                'incoterms': row.get('Incoterms', 'N/A'),
                                'document_currency': row.get('Document_Currency', 'N/A'),
                                'reference_line': row.get('Reference_Line', 'N/A'),
                                'reference_order': row.get('Reference_Order', 'N/A'),
                                'quantity_closed': row.get('Quantity_Closed', 'N/A'),
                                'cumulative_confirmed_qty': row.get('Cummulative_Confirmed_Quantity', 'N/A'),
                                'sales_order_item_value': row.get('Sales_Order_Item_Value', 'N/A'),
                                'open_sales_value': row.get('Open_Sales_Value', 'N/A'),
                                'total_sales_order_value': row.get('Total_Sales_Order_Value', 'N/A'),
                                'created_on': row.get('Created_On', 'N/A')
                            }
                        }
                        aggregated_data['orders'].append(order_detail)
                
                # Add index name to each result and store
                for doc in results:
                    doc['_federated_score'] = doc.get('@search.score', 0)
                    doc['_index_name'] = index_name
                    all_results.append(doc)
                    
        except Exception as e:
            logger.error(f"Federated search failed for index '{index_name}': {e}")
            logger.error(f"Full error: {traceback.format_exc()}")
            continue
    
    # Convert sets to lists for JSON serialization
    aggregated_data['execution_status'] = list(aggregated_data['execution_status'])
    aggregated_data['sales_documents']['types'] = list(aggregated_data['sales_documents']['types'])
    aggregated_data['delivery_metrics']['reliability_scores'] = list(aggregated_data['delivery_metrics']['reliability_scores'])
    aggregated_data['territories']['companies'] = list(aggregated_data['territories']['companies'])
    aggregated_data['territories']['sales_orgs'] = list(aggregated_data['territories']['sales_orgs'])
    aggregated_data['territories']['plants'] = list(aggregated_data['territories']['plants'])
    aggregated_data['territories']['divisions'] = list(aggregated_data['territories']['divisions'])
    aggregated_data['customer_data']['sold_to_parties'] = list(aggregated_data['customer_data']['sold_to_parties'])
    aggregated_data['customer_data']['ship_to_parties'] = list(aggregated_data['customer_data']['ship_to_parties'])
    aggregated_data['customer_data']['countries'] = list(aggregated_data['customer_data']['countries'])
    aggregated_data['customer_data']['regions'] = list(aggregated_data['customer_data']['regions'])
    
    # Sort by score (highest first)
    all_results.sort(key=lambda d: d.get('_federated_score', 0), reverse=True)
    
    # Add the processed sales data as the first result if available
    if aggregated_data['total_orders'] > 0:
        all_results.insert(0, {
            '_index_name': Config.SALES_GENERAL_INDEX[0], # Use the configured sales index
            '_federated_score': float('inf'),  # Ensure it stays at the top
            **aggregated_data
        })

    #log the results
    logger.debug(f"Federated search results: {all_results}")
    
    return all_results

@app.template_filter('regex_match')
def regex_match(value, pattern):
    return bool(re.search(pattern, str(value)))

@app.route('/get_chat_history/<session_id>')
def get_chat_history(session_id):
    try:
        # Get user ID from headers
        user_id = request.headers.get('X-MS-CLIENT-PRINCIPAL-ID')
        if Config.IS_LOCAL_DEV:
            user_id = user_id or Config.get_env('DEBUG_USER_ID', 'local-dev-user')
            
        if not user_id:
            return jsonify({"error": "Authentication required"}), 401
            
        # Get chat session and verify ownership
        chat_session = db.session.get(ChatSession, session_id)
        if not chat_session or chat_session.user_id != user_id:
            return jsonify({"error": "Session not found"}), 404
            
        # Return formatted chat history
        return jsonify({
            "messages": chat_session.chat_history,
            "metadata": {
                "confidence_level": chat_session.confidence_level,
                "product_category": chat_session.product_category,
                "focus_area": chat_session.focus_area,
                "detected_language": chat_session.detected_language
            }
        })
        
    except Exception as e:
        logger.error(f"Error retrieving chat history: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500

# And at the bottom:
if __name__ == '__main__':
    debug_mode = Config.get_env('FLASK_DEBUG', default=False, var_type=bool)
    app.run(debug=debug_mode)
