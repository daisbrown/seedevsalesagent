import json
import datetime
import mimetypes
import traceback
import base64
import asyncio
import requests
import time
import subprocess
import os
import re
import urllib.parse
from typing import Dict, List, Optional
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
from openai import AzureOpenAI
from opencensus.ext.azure import metrics_exporter
from opencensus.stats import aggregation, measure, stats, view
from opencensus.tags import tag_key, tag_map


def get_access_token(resource: str) -> str:
    """
    Get an access token using DefaultAzureCredential.
    Falls back to Azure CLI if necessary.
    
    Args:
        resource (str): The Azure resource requiring authentication.
        
    Returns:
        str: The access token as a Bearer token.
    """
    try:
        credential = DefaultAzureCredential()
        token = credential.get_token(resource)
        return f"Bearer {token.token}"
    except Exception:
        print("[WARNING] DefaultAzureCredential failed, falling back to Azure CLI...")
        return get_cli_token(resource)


def get_cli_token(resource: str) -> str:
    """
    Get access token using Azure CLI for local development.
    
    Args:
        resource (str): The Azure resource requiring authentication.
    
    Returns:
        str: The access token as a Bearer token.
    """
    try:
        result = subprocess.run(
            ['az', 'account', 'get-access-token', '--resource', resource],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise ValueError(f"Failed to get token from Azure CLI: {result.stderr}")

        token_data = json.loads(result.stdout)
        return f"Bearer {token_data['accessToken']}"

    except Exception as e:
        raise ValueError(f"Error getting token from Azure CLI: {e}")


class EmailService:
    # Microsoft Graph API limit for direct file attachments
    MAX_ATTACHMENT_SIZE = 3 * 1024 * 1024  # 3MB

    def __init__(self, config):
        """Initialize EmailService with configuration."""
        self.config = config
        self.graph_api_endpoint = "https://graph.microsoft.com/v1.0"
        
        # Initialize OpenAI client
        self.openai_client = AzureOpenAI(
            azure_endpoint=config.AZURE_OPENAI_ENDPOINT,
            api_key=config.AZURE_OPENAI_KEY,
            api_version=config.AZURE_OPENAI_API_VERSION,
        )
        
        # Initialize blob service client
        account_url = f"https://{config.AZURE_STORAGE_ACCOUNT}.blob.core.windows.net"
        self.credential = DefaultAzureCredential()
        self.blob_service_client = BlobServiceClient(account_url, self.credential)
        self.container_client = self.blob_service_client.get_container_client(
            config.AZURE_STORAGE_CONTAINER_NAME
        )
        
        # Get application's managed identity token for Graph API
        self.access_token = get_access_token("https://graph.microsoft.com")
        
        # Add metrics exporter initialization
        self.metrics_exporter = metrics_exporter.new_metrics_exporter(
            connection_string=config.APPLICATIONINSIGHTS_CONNECTION_STRING
        )
        
        # Create email draft measure and view
        self.email_draft_measure = measure.MeasureInt(
            "email_drafts_created",
            "Number of email drafts created",
            "drafts"
        )
        
        user_key = tag_key.TagKey("user_id")
        
        self.email_draft_view = view.View(
            "email_drafts_created_view",
            "Number of email drafts created by user",
            [user_key],
            self.email_draft_measure,
            aggregation.CountAggregation()
        )
        
        # Register view
        stats.stats.view_manager.register_view(self.email_draft_view)

    def generate_email_content(self, chat_session, citations: List[Dict]) -> Dict:
        """Generate the final, approved email content."""
        try:
            # Get the last few messages for context
            approved_messages = chat_session.chat_history[-10:]
            
            # Extract sales rep details from metadata
            sales_metadata = chat_session.sales_metadata
            sales_rep_info = {
                'name': sales_metadata.get('SalesRepID', 'Sales Representative'),
                'title': sales_metadata.get('Title', 'Sales Representative'),
                'territory': sales_metadata.get('Territory', ''),
                'phone': sales_metadata.get('Phone', ''),
                'email': sales_metadata.get('Email', ''),
                'company': 'Sealed Air Corporation'
            }
            
            # Create prompt
            user_prompt = (
                "Generate a JSON email draft using this conversation and information. "
                "Remember to return ONLY the JSON object, no other text.\n\n"
                "Conversation:\n"
            )
            
            # Add conversation context
            for msg in approved_messages:
                role = "Customer" if msg["role"] == "user" else "Sales Rep"
                user_prompt += f"{role}: {msg['content']}\n\n"
            
            # Add approved attachments
            if citations:
                user_prompt += "\nReference these attachments:\n"
                for i, citation in enumerate(citations, 1):
                    user_prompt += f"{i}. {citation.get('title', 'Untitled Document')}\n"
            
            # Add signature information
            user_prompt += f"\nUse this information for the signature:\n"
            for key, value in sales_rep_info.items():
                user_prompt += f"{key}: {value}\n"

            # Generate content using OpenAI
            response = self.openai_client.chat.completions.create(
                model=self.config.AZURE_OPENAI_DEPLOYMENT,
                messages=[
                    {"role": "system", "content": self._get_email_system_prompt()},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5,
                max_tokens=1000,
                response_format={"type": "json_object"}
            )

            # Parse the response
            email_content = json.loads(response.choices[0].message.content)

            # Transform citations into proper attachment format
            transformed_attachments = self._transform_citations_to_attachments(citations)
            
            # Prepare the attachments
            prepared_attachments = self.prepare_attachments(transformed_attachments)

            return {
                'subject': email_content.get('subject', 'Follow-up from our conversation'),
                'body': email_content.get('body', ''),
                'signature': email_content.get('signature', ''),
                'to': email_content.get('to', ''),
                'cc': email_content.get('cc', []),
                'attachments': transformed_attachments,
                'prepared_attachments': prepared_attachments
            }

        except Exception as e:
            print(f"[ERROR] Failed to generate email content: {e}")
            print(f"[ERROR] Traceback: {traceback.format_exc()}")
            raise

    def _generate_clean_filename(self, original_filename: str, index: int = 1) -> str:
        """
        Generate a simple numbered filename for email attachments.
        
        Args:
            original_filename (str): The original filename (used only for extension)
            index (int): The document number
            
        Returns:
            str: Simple numbered filename with proper extension
        """
        # Get file extension
        _, ext = os.path.splitext(original_filename)
        if not ext:
            ext = '.txt'  # Default extension if none found
            
        return f"Document_{index}{ext}"

    def _transform_citations_to_attachments(self, citations: List[Dict]) -> List[Dict]:
        """
        Transform citation objects into a format suitable for attachment preparation.
        
        Args:
            citations: List of citation dictionaries from the chat
            
        Returns:
            List of attachment dictionaries ready for preparation
        """
        attachments = []
        
        for i, citation in enumerate(citations, 1):
            # Extract the filepath, handling both direct filepath and URL cases
            filepath = citation.get('filepath')
            if not filepath and citation.get('url'):
                # Extract filepath from URL if no direct filepath
                filepath = citation['url'].split('/')[-1]
            
            if filepath:
                # Generate simple numbered filename
                clean_filename = self._generate_clean_filename(filepath, i)
                
                attachment = {
                    'filename': clean_filename,
                    'filepath': filepath,
                    'title': citation.get('title', 'Supporting Document'),
                    'content_type': None  # Will be determined during preparation
                }
                attachments.append(attachment)
        
        return attachments

    def prepare_attachments(self, attachments: List[Dict]) -> List[Dict]:
        """
        Prepare email attachments by downloading files from blob storage.
        Skips attachments larger than Microsoft's 3MB limit for direct file attachments.
        See: https://learn.microsoft.com/en-us/graph/api/resources/fileattachment
        
        Args:
            attachments: List of attachment dictionaries
            
        Returns:
            List of prepared attachment objects with file data
        """
        prepared_attachments = []
        
        try:
            for attachment in attachments:
                prepared = self._prepare_attachment(attachment)
                if prepared:
                    content_length = len(prepared['content'])
                    if content_length > self.MAX_ATTACHMENT_SIZE:
                        print(f"[INFO] Skipping large attachment '{prepared['filename']}' ({content_length} bytes) - exceeds Microsoft's 3MB limit")
                        continue
                    prepared_attachments.append(prepared)
                    
            return prepared_attachments
            
        except Exception as e:
            print(f"[ERROR] Failed to prepare email attachments: {e}")
            print(f"[ERROR] Traceback: {traceback.format_exc()}")
            return []

    def _prepare_attachment(self, citation: Dict) -> Optional[Dict]:
        """Prepare a single attachment for email sending."""
        try:
            filepath = citation.get('filepath')
            if not filepath:
                print(f"[ERROR] No filepath provided in attachment: {citation}")
                return None
                
            # URL decode the filepath and remove /documents/ prefix if present
            filepath = urllib.parse.unquote(filepath)
            if filepath.startswith('/documents/'):
                filepath = filepath[len('/documents/'):]
            print(f"[DEBUG] Cleaned filepath: {filepath}")
            
            # List all blobs with similar names for debugging
            try:
                # Get the filename without any (1) suffix for searching
                search_name = filepath.replace(' (1)', '')
                blobs = list(self.container_client.list_blobs(name_starts_with=search_name.split('[')[0]))
                print(f"[DEBUG] Found {len(blobs)} matching blobs:")
                for blob in blobs:
                    print(f"[DEBUG] - {blob.name}")
            except Exception as e:
                print(f"[ERROR] Failed to list blobs: {str(e)}")
            
            # Try downloading with exact filepath first
            print(f"[DEBUG] Attempting to download blob: {filepath}")
            try:
                blob_client = self.container_client.get_blob_client(filepath)
                blob_data = blob_client.download_blob()
                content = blob_data.readall()
                print(f"[DEBUG] Successfully downloaded blob: {filepath}")
            except Exception as e:
                print(f"[ERROR] Failed to download blob {filepath}: {e}")
                
                # If the filepath contains (1), try without it
                if ' (1)' in filepath:
                    alt_filepath = filepath.replace(' (1)', '')
                    print(f"[DEBUG] Trying without (1): {alt_filepath}")
                    try:
                        blob_client = self.container_client.get_blob_client(alt_filepath)
                        blob_data = blob_client.download_blob()
                        content = blob_data.readall()
                        print(f"[DEBUG] Successfully downloaded blob: {alt_filepath}")
                    except Exception as e2:
                        print(f"[ERROR] Failed to download blob {alt_filepath}: {e2}")
                        return None
                else:
                    # If no (1) in original path, try adding it before the extension
                    name, ext = os.path.splitext(filepath)
                    alt_filepath = f"{name} (1){ext}"
                    print(f"[DEBUG] Trying with (1): {alt_filepath}")
                    try:
                        blob_client = self.container_client.get_blob_client(alt_filepath)
                        blob_data = blob_client.download_blob()
                        content = blob_data.readall()
                        print(f"[DEBUG] Successfully downloaded blob: {alt_filepath}")
                    except Exception as e2:
                        print(f"[ERROR] Failed to download blob {alt_filepath}: {e2}")
                        return None

            # Ensure we have valid content
            if not content:
                print(f"[ERROR] No content downloaded for {filepath}")
                return None
            
            content_type = blob_data.properties.content_settings.content_type
            if not content_type:
                content_type, _ = mimetypes.guess_type(filepath)
                content_type = content_type or 'application/octet-stream'
                
            print(f"[DEBUG] Content type determined: {content_type}")

            return {
                'filename': citation.get('filename', filepath.split('/')[-1]),
                'content': content,
                'content_type': content_type,
                'title': citation.get('title', 'Supporting Document')
            }
            
        except Exception as e:
            print(f"[ERROR] Failed to prepare attachment: {e}")
            print(f"[ERROR] Full error details: {traceback.format_exc()}")
            return None

    async def create_draft(self, email_package: Dict, user_email: str, access_token: str) -> Dict:
        """Create an email draft using Graph REST API."""
        try:
            if not user_email:
                raise ValueError("User email is required to create email drafts")

            # Add debug logging
            if email_package.get('prepared_attachments'):
                print(f"[DEBUG] Number of prepared attachments: {len(email_package['prepared_attachments'])}")
                for idx, attachment in enumerate(email_package['prepared_attachments']):
                    print(f"[DEBUG] Attachment {idx + 1}:")
                    print(f"  - Filename: {attachment.get('filename')}")
                    print(f"  - Content Type: {attachment.get('content_type')}")
                    print(f"  - Content Length: {len(attachment['content']) if attachment.get('content') else 0} bytes")

            # Create message payload
            message = {
                "subject": email_package['subject'],
                "importance": "normal",
                "isDraft": True,
                "body": {
                    "contentType": "text",
                    "content": email_package['body']
                },
                "toRecipients": [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in email_package['to'].split(';') if addr.strip()
                ],
                "ccRecipients": [
                    {"emailAddress": {"address": addr.strip()}}
                    for addr in email_package.get('cc', []) if addr.strip()
                ],
                "from": {
                    "emailAddress": {
                        "address": user_email
                    }
                }
            }

            # Handle attachments
            if email_package.get('prepared_attachments'):
                message["attachments"] = []
                for attachment in email_package['prepared_attachments']:
                    if attachment.get('content') and attachment.get('filename'):
                        message["attachments"].append({
                            "@odata.type": "#microsoft.graph.fileAttachment",
                            "name": attachment['filename'],
                            "contentType": attachment.get('content_type', 'application/octet-stream'),
                            "contentBytes": base64.b64encode(attachment['content']).decode('utf-8')
                        })

            # Make API request using the user's access token
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(
                f"{self.graph_api_endpoint}/users/{user_email}/messages",
                headers=headers,
                json=message
            )
            response.raise_for_status()
            result = response.json()

            return {
                "success": True,
                "message_id": result.get('id'),
                "status": "Draft created successfully",
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "location": "Drafts folder"
            }

        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Graph API request failed: {e}")
            print(f"[ERROR] Response: {e.response.text if hasattr(e, 'response') else 'No response'}")
            return {
                "success": False,
                "error": str(e),
                "status": "Failed to create draft - API error",
                "timestamp": datetime.datetime.utcnow().isoformat()
            }

    def create_email_draft_sync(self, email_package: Dict, user_email: str = None, access_token: str = None) -> Dict:
        """Synchronous wrapper for creating email drafts."""
        try:
            if not user_email:
                raise ValueError("User email is required to create email drafts")
            
            result = asyncio.run(self.create_draft(email_package, user_email, access_token))
            
            # Only record metric if draft was created successfully
            if result.get('success'):
                mmap = stats.stats.stats_recorder.new_measurement_map()
                tmap = tag_map.TagMap()
                tmap.insert("user_id", user_email)
                mmap.measure_int_put(self.email_draft_measure, 1)
                mmap.record(tmap)
                
                # Export the metrics
                self.metrics_exporter.export_metrics([])
            
            return result
            
        except Exception as e:
            print(f"[ERROR] Failed to create email draft sync: {e}")
            print(f"[ERROR] Traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "error": str(e),
                "status": "Failed to create draft",
                "timestamp": datetime.datetime.utcnow().isoformat()
            }

    def _get_email_system_prompt(self) -> str:
        """Get the system prompt for email generation."""
        return """
        You are preparing an email draft. Respond ONLY with a JSON object in this format:
        {
            "subject": "Brief subject line",
            "body": "Main email content",
            "signature": "Professional signature block",
            "to": "customer@example.com",
            "cc": []
        }
        
        Guidelines:
        - Write in first person from the sales rep's perspective
        - Keep the subject line clear and concise
        - Include all key points from the discussion
        - Reference any attachments in the body
        - Format the signature professionally with all contact details
        """
