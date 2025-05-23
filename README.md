# Chat Application with Azure OpenAI, Azure Search, and Sales Rep Context

This Flask application integrates with Azure OpenAI, Azure Cognitive Search, and an SQLite database (stored in Azure Web Apps). The app provides a chat interface that automatically incorporates a user's sales rep context into system prompts. Users can upload images, track conversation history, and retrieve relevant documents via citations stored in Azure Blob Storage.

## Table of Contents

1. [Overview](#overview)  
2. [Key Features](#key-features)  
3. [Requirements](#requirements)  
   - [Required Python Packages](#required-python-packages)
   - [Local Development Requirements](#local-development-requirements)
   - [Environment Variables](#environment-variables)
4. [Installation & Setup](#installation--setup)  
5. [Configuration](#configuration)  
   - [Environment Variable Configuration](#environment-variable-configuration)
   - [Required Environment Variables](#required-environment-variables)
   - [Optional Environment Variables](#optional-environment-variables)
6. [Database Initialization](#database-initialization)  
7. [Usage & Endpoints](#usage--endpoints)  
   - [Root Route (/)](#root-route-)
   - [Message Handling (/message)](#message-handling-message)
   - [New Chat (/new_chat)](#new-chat-new_chat)
   - [Switch Chat (/switch_chat)](#switch-chat-switch_chat)
   - [Serving Documents (/documents/<path:filename>)](#serving-documents-documentspathfilename)
8. [Sales Rep Context & Caching](#sales-rep-context--caching)  
9. [Error Handling](#error-handling)  
10. [Troubleshooting & FAQ](#troubleshooting--faq)  

11. [Contributing](#contributing)  

## Overview

This application handles user chats in a multi-step pipeline:

1. **User Authentication**  
   Extracts user credentials from X-MS-CLIENT-PRINCIPAL-ID and X-MS-CLIENT-PRINCIPAL-NAME HTTP headers (typical in Azure App Service or Azure Static Web Apps with Authentication/Authorization).

2. **Azure OpenAI Chat**  
   Uses the Azure OpenAI API to handle conversation logic, including context infusion from Azure Cognitive Search (for citations) and sales metadata from a specialized Azure Cognitive Search index for sales reps.

3. **Database-Backed Chat Sessions**  
   Stores conversation history (both full system messages and user-facing chat messages) in a ChatSession SQLite model, preserving context for each user session.

4. **Sales Rep Context**  
   Dynamically fetches and caches sales rep data (territory, performance, client accounts, etc.) from an Azure Cognitive Search index. This data then populates a placeholder ([SALES REP CONTEXT HERE]) in the system prompt.

5. **Image Upload & Processing**  
   Allows users to upload JPEG/PNG images and incorporate them into the chat conversation, which is then sent to Azure OpenAI for further handling.

6. **Document Serving**  
   Citations returned by the Azure OpenAI Chat API can be downloaded from Azure Blob Storage on demand.

## Key Features

1. **User & Session Management**
   - Automatic session creation and retrieval based on user ID from headers
   - Session ID stored in Flask's server-side session object

2. **Chat Functionality**
   - Maintains separate conversation contexts for each user, stored in the ChatSession model
   - Truncates old messages beyond a configurable limit (MESSAGE_HISTORY_LIMIT)

3. **Azure OpenAI Integration**  
   - Uses Azure OpenAI's chat endpoint with a custom system prompt that is dynamically updated with the user's sales context.  
   - Supports citations from an Azure Cognitive Search index.  

4. **Azure Cognitive Search**  
   - Queries an index for both sales rep data (e.g., territory, performance stats) and general documents for context retrieval.  
   - Caches sales rep data to reduce repeated calls.  

5. **File Handling**  
   - Supports JPEG/PNG image uploads up to 8MB.  
   - Validates MIME types, performs in-memory transformations, and includes the image in chat messages.  

6. **Document Retrieval**  
   - Serves documents (PDFs, images, etc.) from Azure Blob Storage via /documents/<path:filename>.

## Requirements

### Required Python Packages
- Python 3.8+
- Flask ^2.0
- Flask-SQLAlchemy ^3.0
- Azure OpenAI SDK
- Azure Storage Blob ^12.0
- Azure Identity ^1.0
- Requests ^2.0
- Pillow ^9.0

### Local Development Requirements
1. **Local Environment Setup**
   ```env
   # Flask configuration
   FLASK_SECRET_KEY=your-secret-key-here
   FLASK_DEBUG=True
   IS_LOCAL_DEV=True

   # Debug Configuration (Required for local auth bypass)
   DEBUG_USER_ID=your-user-id
   DEBUG_USER_EMAIL=your-email@example.com
   DEBUG_USER_GROUPS='["your-group-id"]'
   ```

2. **Azure Services Access**
   - Azure OpenAI service instance with API access
   - Azure Cognitive Search instance
   - Azure Blob Storage account
   - Appropriate role assignments and permissions

### Environment Variables
```env
# Azure OpenAI Configuration
AZURE_OPENAI_ENDPOINT=https://<your-openai-resource>.openai.azure.com
AZURE_OPENAI_KEY=<your-azure-openai-key>
AZURE_OPENAI_API_VERSION=2024-05-01-preview
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_MESSAGE_HISTORY_LIMIT=10
AZURE_OPENAI_TEMPERATURE=0.6
AZURE_OPENAI_MAX_COMPLETION_TOKENS=8000
AZURE_OPENAI_MAX_TOTAL_TOKENS=32000
AZURE_OPENAI_MAX_PROMPT_TOKENS=128000

# Azure AI Search Configuration
AZURE_AI_SEARCH_ENDPOINT=https://<your-search>.search.windows.net
AZURE_AI_SEARCH_KEY=<your-search-key>
AZURE_OPENAI_SEARCH_INDEX=<documents>
AZURE_OPENAI_SEARCH_SALESREP_INDEX=<salesrepsindex>

# Azure Blob Storage Configuration
AZURE_STORAGE_ACCOUNT=<your-storage-account>
AZURE_STORAGE_CONTAINER_NAME=<your-container>
AZURE_STORAGE_CONTAINER_TELEMETRY_NAME=<your-telemetry-container>
AZURE_STORAGE_CONTAINER_FEEDBACK_NAME=<your-feedback-container>

# Group IDs for Local Testing
SALES_GENERAL_GROUP_ID=<your-general-group-id>
SALES_SPECIAL_GROUP_ID=<your-special-group-id>
SALES_MANAGEMENT_GROUP_ID=<your-management-group-id>

# Group to Index Mapping for Local Testing
SALES_GENERAL_INDEX=<your-general-index>
SALES_SPECIAL_INDEX=<your-special-index>
SALES_MANAGEMENT_INDEX=<your-management-index>

# Performance and Cache Settings
EMPTY_CHAT_TIMEOUT=3600
SALES_DATA_REFRESH_INTERVAL_SECONDS=3600
```

### Important Notes
- Never commit your `.env` file to version control
- Keep your API keys and secrets secure
- The `IS_LOCAL_DEV=True` flag enables local development features
- `DEBUG_USER_GROUPS` must be a valid JSON string array
- Make sure your Azure service endpoints are accessible from your local network
- Consider using Azure Storage Emulator for local blob storage testing

## Installation & Setup

1. Clone the Repository
   ```bash
   git clone https://github.com/<username>/<your-repo>.git
   cd your-repo
   ```

2. Create a Virtual Environment (recommended)  
   ```bash
   python -m venv venv
   source venv/bin/activate      # On Windows: .\venv\Scripts\activate
   ```

3. Install Dependencies  
   ```bash
   pip install -r requirements.txt
   ```

4. Set Required Environment Variables  
   - Create a `.env` file in the project root directory with the following content:
     
     ```env
     # Azure OpenAI Configuration
     AZURE_OPENAI_ENDPOINT=https://<your-openai-resource>.openai.azure.com
     AZURE_OPENAI_KEY=<your-azure-openai-key>
     AZURE_OPENAI_API_VERSION=2024-02-15-preview
     AZURE_OPENAI_DEPLOYMENT=<gpt-4o>  

     # Azure AI Search Configuration
     AZURE_AI_SEARCH_ENDPOINT=https://<your-search>.search.windows.net
     AZURE_AI_SEARCH_KEY=<your-search-key>
     AZURE_OPENAI_SEARCH_INDEX=documents
     AZURE_OPENAI_SEARCH_SALESREP_INDEX=salesrepsindex

     # Azure Blob Storage Configuration
     AZURE_STORAGE_ACCOUNT=<your-storage-account>
     AZURE_STORAGE_CONTAINER_NAME=<your-container>
     AZURE_STORAGE_CONTAINER_TELEMETRY_NAME=<your-container>
     AZURE_STORAGE_CONTAINER_FEEDBACK_NAME=<your-container>

     # Flask Configuration
     FLASK_SECRET_KEY=<your-secret>

     # Debug Configuration (for local development)
     DEBUG_USER_ID=<your-debug-user-id>
     DEBUG_USER_EMAIL=<your-debug-email>
     DEBUG_USER_GROUPS='["<your-group-id>"]'  # Must be valid JSON array string
     FLASK_DEBUG=True
     IS_LOCAL_DEV=True

     # Group IDs for Local Testing
     SALES_GENERAL_GROUP_ID=<your-general-group-id>  
     SALES_SPECIAL_GROUP_ID=<your-special-group-id> 
     SALES_MANAGEMENT_GROUP_ID=<your-management-group-id>

     # Group to Index Mapping for Local Testing
     SALES_GENERAL_INDEX=<your-general-index>
     SALES_SPECIAL_INDEX=<your-special-index> 
     SALES_MANAGEMENT_INDEX=<your-management-index>  

     ```

   - The application will automatically load these variables from the `.env` file on startup
   - For production deployment (e.g., Azure App Service), configure these variables in your cloud platform's environment settings
   - Make sure to add `.env` to your `.gitignore` file to prevent committing sensitive information

5. Run the App  
   ```bash
   python app.py
   ```

   By default, Flask runs at http://127.0.0.1:5000. Depending on your deployment environment, you might run gunicorn or uvicorn instead.

## Configuration

Key configuration points inside the application:

- **Environment Variables**  
  - The application uses a `Config` class to manage environment variables with type validation and defaults
  - Environment variables can be loaded from a `.env` file or system environment
  - Required variables are validated on startup
  - Sensitive values are masked in logs

- **Database**  
  - Locates/creates an SQLite database at `~/site/wwwroot/chat_sessions.db` in Azure Web Apps
  - Requires the db folder to be writable
  - Automatically initializes tables on startup (configurable)

- **Azure Services**  
  - Azure OpenAI credentials and endpoints are read from environment variables
  - Azure Cognitive Search queries are configured via environment variables
  - Azure Blob Storage is used for document serving, also configured via environment variables

- **Chat Parameters**  
  - `MESSAGE_HISTORY_LIMIT` to set how many messages are kept in the prompt
  - `TEMPERATURE` to control the creativity of the GPT-based responses
  - `MAX_FILE_SIZE` and `ALLOWED_MIME_TYPES` for controlling image uploads
  - `EMPTY_CHAT_TIMEOUT` for managing unused chat sessions
  - `SALES_DATA_REFRESH_INTERVAL` for caching sales rep data

- **System Prompt**  
  - Defined in `config.py` as `SYSTEM_PROMPT`
  - A placeholder `[SALES REP CONTEXT HERE]` is replaced dynamically with the user's territory, clients, and other metadata
  - System prompts are validated and standardized through helper functions

- **Token Management**  
  - Configurable limits for prompt, completion, and total tokens
  - Warning threshold for token usage (default 90%)
  - Automatic message truncation to stay within limits

- **Caching**  
  - Sales rep data is cached with configurable refresh interval
  - Cache invalidation based on time and data freshness
  - Thread-safe cache updates using locks

### Environment Variable Configuration

The application uses a robust configuration system that:

1. **Loads Variables**  
   ```python
   # From .env file if present
   load_dotenv(env_path)
   
   # From system environment
   os.environ.get(key, default)
   ```

2. **Validates Types**  
   ```python
   # Example type validation
   AZURE_OPENAI_MESSAGE_HISTORY_LIMIT = get_env('AZURE_OPENAI_MESSAGE_HISTORY_LIMIT', 10, var_type=int)
   AZURE_OPENAI_TEMPERATURE = get_env('AZURE_OPENAI_TEMPERATURE', 0.7, var_type=float)
   ```

3. **Handles Required Variables**  
   ```python
   # Required variables raise errors if missing
   AZURE_OPENAI_ENDPOINT = get_env('AZURE_OPENAI_ENDPOINT', required=True)
   ```

4. **Provides Defaults**  
   ```python
   # Sensible defaults for optional variables
   FLASK_SECRET_KEY = get_env('FLASK_SECRET_KEY', os.urandom(24))
   ```

5. **Logs Configuration**  
   - Logs all non-sensitive configuration values on startup
   - Masks sensitive values (API keys, secrets) in logs

### Required Environment Variables

```env
# Azure OpenAI Configuration
AZURE_OPENAI_ENDPOINT=https://<your-openai-resource>.openai.azure.com
AZURE_OPENAI_KEY=<your-azure-openai-key>
AZURE_OPENAI_API_VERSION=2024-02-15-preview
AZURE_OPENAI_DEPLOYMENT=<gpt-4>  # or gpt-35-turbo

# Azure AI Search Configuration
AZURE_AI_SEARCH_ENDPOINT=https://<your-search>.search.windows.net
AZURE_AI_SEARCH_KEY=<your-search-key>
AZURE_OPENAI_SEARCH_INDEX=<documents>
AZURE_OPENAI_SEARCH_SALESREP_INDEX=<salesrepsindex>

# Azure Blob Storage Configuration
AZURE_STORAGE_ACCOUNT=<your-storage-account>
AZURE_STORAGE_CONTAINER_NAME=<your-container>
AZURE_STORAGE_CONTAINER_TELEMETRY_NAME=<your-container>
AZURE_STORAGE_CONTAINER_FEEDBACK_NAME=<your-container>

# Flask Configuration
FLASK_SECRET_KEY=<your-secret>
```

### Optional Environment Variables

```env
# Chat Control
AZURE_OPENAI_MESSAGE_HISTORY_LIMIT=10
AZURE_OPENAI_TEMPERATURE=0.7
EMPTY_CHAT_TIMEOUT=3600
SALES_DATA_REFRESH_INTERVAL_SECONDS=3600

# Token Limits
AZURE_OPENAI_MAX_COMPLETION_TOKENS=4096
AZURE_OPENAI_MAX_TOTAL_TOKENS=500000
AZURE_OPENAI_MAX_PROMPT_TOKENS=490000

# Application Insights (Optional)
APPLICATIONINSIGHTS_CONNECTION_STRING=<your-connection-string>

# Debug Configuration (Optional)
DEBUG_USER_ID=<your-debug-user-id>
DEBUG_USER_EMAIL=<your-debug-email>
```

## Database Initialization

The app automatically initializes (and re-initializes) the database on startup with:
python
def init_db():
    # Drop existing tables and recreate them
    db.drop_all()
    db.create_all()

If you do not want to drop the tables every time, modify the code to remove the drop_all() call or handle migrations more carefully.

## Usage & Endpoints

Below are the main routes you'll interact with. Remember that Azure App Service or similar might inject authentication headers automatically:

### Root Route (/)

- **Method**: GET  
- **Purpose**: Renders the main chat interface (chat.html) and initializes or resumes a ChatSession.  
- **Behavior**:  
  1. Checks for user authentication via X-MS-CLIENT-PRINCIPAL-ID and X-MS-CLIENT-PRINCIPAL-NAME.  
  2. Loads any existing chat sessions for this user ID from the database.  
  3. Creates a new chat session if none exists, or if the current session doesn't belong to the user.  
  4. Injects the chat history, sales metadata, and conversation list into the template.  

### Message Handling (/message)

- **Method**: POST  
- **Purpose**: Processes a new user message (or uploaded image) and returns a GPT-based response.  
- **Behavior**:  
  1. Retrieves the existing ChatSession by session ID from Flask's session.  
  2. Appends the user's text/image to the conversation messages.  
  3. Calls Azure OpenAI's chat.completions.create with a system prompt that includes sales context.  
  4. Gathers citations from the response, updates conversation history, and returns a JSON response with:
     
json
     {
       "response": "Assistant's message",
       "metadata": { ... },
       "citations": [ ... ]
     }

  5. If an image is uploaded, the file is base64-encoded and sent as part of the user message.  

### New Chat (/new_chat)

- **Method**: POST  
- **Purpose**: Creates a brand-new chat session for the user, ensuring no existing empty chats are active.  
- **Behavior**:  
  1. Prevents spam creation if there's an existing empty (unused) session that is still valid.  
  2. Cleans up old empty sessions beyond a configurable timeout (EMPTY_CHAT_TIMEOUT).  
  3. Returns a JSON object indicating success or error.

### Switch Chat (/switch_chat)

- **Method**: POST  
- **Purpose**: Switches the current session to a different ChatSession by ID.  
- **Behavior**:  
  - Updates session['session_id'] to the chosen conversation.  
  - Returns the session's sales rep metadata in JSON if found.

### Serving Documents (/documents/<path:filename>)

- **Method**: GET  
- **Purpose**: Retrieves a blob from Azure Storage (PDFs, images, etc.) that were referenced as citations in the chat.  
- **Behavior**:  
  1. Validates the requested filename to avoid path traversal.  
  2. Tries to fetch the blob from Azure Blob Storage via BlobServiceClient.  
  3. If found, streams the file back to the browser with an appropriate content type.  
  4. If not found, returns a 404 message.

## Sales Rep Context & Caching

- The function find_sales_rep_by_email(email) queries a dedicated Azure Search index for a matching sales rep.  
- Results (territory, performance, client accounts, etc.) are cached in CACHE_CONFIG['sales_data_cache'] to reduce repetitive lookups.  
- The system prompt includes a placeholder [SALES REP CONTEXT HERE] that is replaced by actual sales data to contextualize GPT answers.

## Error Handling

- **Database Errors**  
  - Handled via safe_commit() which rolls back on SQLAlchemyError.  
- **Image Upload**  
  - Ensures file size is under MAX_FILE_SIZE and MIME type is in ALLOWED_MIME_TYPES.  
- **Missing Headers**  
  - Root route (/) returns 401 if user credentials are not provided.  

For more detail, see the print statements throughout the code to trace issues and debugging information in your logs.

## Troubleshooting & FAQ

1. **I can't authenticate users locally**  
   - This code expects headers X-MS-CLIENT-PRINCIPAL-ID and X-MS-CLIENT-PRINCIPAL-NAME typically provided by Azure. For local dev, you can:
     - Set DEBUG_USER_ID and DEBUG_USER_EMAIL in your .env file to bypass authentication

2. **Documents not found in Blob Storage**  
   - Ensure your AZURE_STORAGE_ACCOUNT and container name are correct.  
   - Verify that the requested blob name matches exactly (case-sensitive).  

3. **ChatSession table is dropped every time the app restarts**  
   - Remove or modify db.drop_all() in init_db() if you want persistent data across restarts.

4. **Error ResourceNotFoundError for Azure Blob**  
   - Make sure the requested filename is correctly URL-decoded. Check the logs for [DEBUG] Decoded filename:.  

5. **AzureSearch or AzureOpenAI Key Issues**  
   - Double check environment variables. The code logs the endpoints and keys if necessary but be mindful of not leaking secrets in production logs.

## Contributing

1. **Fork** the repository.  
2. **Create a new branch** for your feature or bug fix.  
3. **Commit** changes with descriptive messages.  
4. **Submit a Pull Request** summarizing your changes.  

We welcome issues and pull requests that improve the codebase, documentation, or implement new features.

Feel free to open issues or pull requests for enhancements or bugs.

###