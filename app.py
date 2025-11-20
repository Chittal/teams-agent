import asyncio
import re
import uuid
import logging
import json
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from azure.identity import ManagedIdentityCredential, ClientSecretCredential
from langchain_groq import ChatGroq
from microsoft.teams.api import MessageActivity, TypingActivityInput
from microsoft.teams.apps import ActivityContext, App
from microsoft.teams.cards import AdaptiveCard
from config import Config

config = Config()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

# Initialize Groq LLM
groq_llm = ChatGroq(
    groq_api_key=config.GROQ_API_KEY,
    model_name="llama-3.1-8b-instant",  # You can change this to other Groq models
    temperature=0.7,
)

def create_token_factory():
    """Token factory for Azure Managed Identity"""
    def get_token(scopes, tenant_id=None):
        credential = ManagedIdentityCredential(client_id=config.APP_ID)
        if isinstance(scopes, str):
            scopes_list = [scopes]
        else:
            scopes_list = scopes
        token = credential.get_token(*scopes_list)
        return token.token
    return get_token

def create_client_secret_token_factory():
    """Token factory for Client Secret authentication (for Render/AWS)"""
    def get_token(scopes, tenant_id=None):
        credential = ClientSecretCredential(
            tenant_id=config.APP_TENANTID or "common",
            client_id=config.APP_ID,
            client_secret=config.APP_PASSWORD
        )
        if isinstance(scopes, str):
            scopes_list = [scopes]
        else:
            scopes_list = scopes
        token = credential.get_token(*scopes_list)
        return token.token
    return get_token

# Initialize App with authentication
# For Azure with Managed Identity
if config.APP_TYPE == "UserAssignedMsi" and config.APP_ID:
    app = App(token=create_token_factory())
# For Render/AWS with Client Secret authentication
elif config.APP_ID and config.APP_PASSWORD:
    app = App(token=create_client_secret_token_factory())
# Fallback - will cause 401 errors without credentials
else:
    app = App()

@app.on_message_pattern(re.compile(r"hello|hi|greetings"))
async def handle_greeting(ctx: ActivityContext[MessageActivity]) -> None:
    """Handle greeting messages."""
    await ctx.send("Hello! How can I assist you today?")

async def fetch_open_graph_metadata(url: str) -> dict:
    """Fetch Open Graph metadata from a URL."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()
            html = response.text
            soup = BeautifulSoup(html, 'html.parser')
            
            og_data = {
                'title': None,
                'description': None,
                'image': None,
                'url': url
            }
            
            # Extract Open Graph tags
            og_title = soup.find('meta', property='og:title')
            if og_title:
                og_data['title'] = og_title.get('content')
            
            og_description = soup.find('meta', property='og:description')
            if og_description:
                og_data['description'] = og_description.get('content')
            
            og_image = soup.find('meta', property='og:image')
            if og_image:
                image_url = og_image.get('content')
                # Handle relative URLs
                if image_url and not image_url.startswith(('http://', 'https://')):
                    parsed = urlparse(url)
                    base_url = f"{parsed.scheme}://{parsed.netloc}"
                    if image_url.startswith('/'):
                        og_data['image'] = base_url + image_url
                    else:
                        og_data['image'] = f"{base_url}/{image_url}"
                else:
                    og_data['image'] = image_url
            
            # Fallback to regular meta tags if OG tags not found
            if not og_data['title']:
                title_tag = soup.find('title')
                if title_tag:
                    og_data['title'] = title_tag.string
            
            if not og_data['description']:
                meta_desc = soup.find('meta', attrs={'name': 'description'})
                if meta_desc:
                    og_data['description'] = meta_desc.get('content')
            
            return og_data
    except Exception as e:
        logger.error(f"Error fetching OG metadata: {e}")
        return {
            'title': None,
            'description': None,
            'image': None,
            'url': url
        }

def create_og_preview_card(og_data: dict) -> AdaptiveCard:
    """Create an AdaptiveCard that mimics Open Graph preview."""
    body_elements = []
    
    # Add image if available
    if og_data.get('image'):
        body_elements.append({
            "type": "Image",
            "url": og_data['image'],
            "size": "Large",
            "spacing": "Medium"
        })
    
    # Add title
    if og_data.get('title'):
        body_elements.append({
            "type": "TextBlock",
            "text": og_data['title'],
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
            "spacing": "Small"
        })
    
    # Add description
    if og_data.get('description'):
        body_elements.append({
            "type": "TextBlock",
            "text": og_data['description'],
            "wrap": True,
            "spacing": "Small",
            "size": "Small"
        })
    
    # Extract domain from URL
    parsed = urlparse(og_data['url'])
    domain = parsed.netloc
    
    # Add domain/source
    body_elements.append({
        "type": "TextBlock",
        "text": domain,
        "size": "Small",
        "color": "Accent",
        "spacing": "Small"
    })
    
    adaptive_card = AdaptiveCard(
        version="1.4",
        body=body_elements,
        actions=[
            {
                "type": "Action.OpenUrl",
                "title": "Open Link",
                "url": og_data['url']
            }
        ]
    )
    return adaptive_card

@app.on_message_pattern(re.compile(r"open graph protocol test"))
async def open_graph_metatags(ctx: ActivityContext[MessageActivity]) -> None:
    """Handle open graph metatags."""
    url = "https://ogp.me/"
    og_data = await fetch_open_graph_metadata(url)
    card = create_og_preview_card(og_data)
    await ctx.send(card)

def create_webpage_card(url: str, title: str = "Open Webpage", description: str = "Click the button below to open the webpage", image_url: str = None):
    """Create an Adaptive Card with an OpenUrl action to render a webpage."""
    body_elements = [
        {
            "type": "TextBlock",
            "text": title,
            "weight": "Bolder",
            "size": "Medium"
        }
    ]
    
    # Add image if provided
    if image_url:
        body_elements.append({
            "type": "Image",
            "url": image_url,
            "size": "Stretch",  # Stretch fills the full width of the card
            "spacing": "Medium"
        })
    
    body_elements.append({
        "type": "TextBlock",
        "text": description,
        "wrap": True,
        "spacing": "Small"
    })
    
    adaptive_card = AdaptiveCard(
        version="1.4",
        body=body_elements,
        actions=[
            {
                "type": "Action.OpenUrl",
                "title": "Open Dashboard",
                "url": url,
                "style": "positive"
            }
        ]
    )
    return adaptive_card

@app.on_message_pattern(re.compile(r"dashboard", re.IGNORECASE))
async def handle_webpage_request(ctx: ActivityContext[MessageActivity]) -> None:
    """Handle requests to render a webpage."""
    # Default webpage URL - you can customize this or extract from message
    webpage_url = "https://claude.ai/artifacts/1f4899b1-da1d-4712-aaaa-26514ec4635d"  # Replace with your desired URL
    
    # You can also extract URL from the message if provided
    # For example, if user says "show webpage https://example.com"
    # url_match = re.search(r'https?://[^\s]+', ctx.activity.text)
    # if url_match:
    #     webpage_url = url_match.group(0)
    
    # Ensure the URL is absolute and properly formatted
    # if not webpage_url.startswith(('http://', 'https://')):
    #     webpage_url = 'https://' + webpage_url
    
    adaptive_card = create_webpage_card(
        url=webpage_url,
        title="Dashboard",
        description="Click the button below to open the dashboard in your browser.",
        image_url="https://learn.microsoft.com/en-us/power-bi/create-reports/media/service-dashboards/power-bi-dashboard2.png"
    )
    
    # Send AdaptiveCard directly - ctx.send() accepts AdaptiveCard and handles it automatically
    await ctx.send(adaptive_card)


# Slash Command Handlers
async def handle_help_command(ctx: ActivityContext[MessageActivity]) -> None:
    """Handle /help command - Show available commands."""
    help_card = AdaptiveCard(
        version="1.4",
        body=[
            {
                "type": "TextBlock",
                "text": "Available Commands",
                "weight": "Bolder",
                "size": "Large",
                "spacing": "Medium"
            },
            {
                "type": "TextBlock",
                "text": "/help - Show available commands",
                "wrap": True,
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": "/search [query] - Search for information using AI",
                "wrap": True,
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": "/status - Check bot status",
                "wrap": True,
                "spacing": "Small"
            },
            {
                "type": "TextBlock",
                "text": "You can also chat with me naturally - I'll use AI to help answer your questions!",
                "wrap": True,
                "spacing": "Medium",
                "isSubtle": True,
                "size": "Small"
            }
        ]
    )
    await ctx.send(help_card)


async def handle_search_command(ctx: ActivityContext[MessageActivity], args: list) -> None:
    """Handle /search command - Search for information using AI."""
    if not args:
        await ctx.send("Please provide a search query. Usage: /search [your query]")
        return
    
    query = ' '.join(args)
    await ctx.reply(TypingActivityInput())
    
    try:
        # Use Groq LLM to search/answer the query
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, groq_llm.invoke, f"Search and provide information about: {query}")
        answer = response.content if hasattr(response, 'content') else str(response)
        
        # Create a card with the search results
        search_card = AdaptiveCard(
            version="1.4",
            body=[
                {
                    "type": "TextBlock",
                    "text": f"Search Results: {query}",
                    "weight": "Bolder",
                    "size": "Medium",
                    "spacing": "Medium"
                },
                {
                    "type": "TextBlock",
                    "text": answer,
                    "wrap": True,
                    "spacing": "Small"
                }
            ]
        )
        await ctx.send(search_card)
    except Exception as e:
        logger.error(f"Error in search command: {e}")
        await ctx.send(f"I encountered an error while searching: {str(e)}. Please try again.")


async def handle_status_command(ctx: ActivityContext[MessageActivity]) -> None:
    """Handle /status command - Check bot status."""
    status_card = AdaptiveCard(
        version="1.4",
        body=[
            {
                "type": "TextBlock",
                "text": "Bot Status",
                "weight": "Bolder",
                "size": "Large",
                "spacing": "Medium"
            },
            {
                "type": "TextBlock",
                "text": "✅ Bot is running and ready!",
                "wrap": True,
                "spacing": "Small",
                "color": "Good"
            },
            {
                "type": "TextBlock",
                "text": "ReKnew AI Teams Bot is operational and ready to assist you.",
                "wrap": True,
                "spacing": "Small",
                "isSubtle": True
            }
        ]
    )
    await ctx.send(status_card)


def is_slash_command(text: str) -> bool:
    """Check if the message is a slash command."""
    return text.strip().startswith('/')


def parse_slash_command(text: str) -> tuple[str, list]:
    """Parse slash command and return (command, args)."""
    text = text.strip()
    if not text.startswith('/'):
        return None, []
    
    parts = text[1:].split()
    if not parts:
        return None, []
    
    command = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []
    return command, args


@app.on_message
async def handle_message(ctx: ActivityContext[MessageActivity]):
    """Handle message activities - routes slash commands or uses Groq LLM."""
    await ctx.reply(TypingActivityInput())
    logger.debug(f"Received message: {ctx.activity}")
    logger.debug(f"Received message: {json.dumps(ctx.activity.__dict__, default=str, indent=2)}")

    user_message = ctx.activity.text
    
    # Check if message is a slash command
    if is_slash_command(user_message):
        command, args = parse_slash_command(user_message)
        
        if command == 'help':
            await handle_help_command(ctx)
        elif command == 'search':
            await handle_search_command(ctx, args)
        elif command == 'status':
            await handle_status_command(ctx)
        else:
            # Unknown command
            await ctx.send(f"Unknown command: /{command}. Type /help to see available commands.")
        return
    
    # Handle regular messages with Groq LLM
    try:
        # Run Groq LLM in executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, groq_llm.invoke, user_message)
        answer = response.content if hasattr(response, 'content') else str(response)
        
        # Send the AI response
        await ctx.send(answer)
    except Exception as e:
        # Handle errors gracefully
        logger.error(f"Error in LLM processing: {e}")
        error_message = f"I encountered an error: {str(e)}. Please try again."
        await ctx.send(error_message)

    # if "reply" in ctx.activity.text.lower():
    #     await ctx.reply("Hello! How can I assist you today?")
    # else:
    #     await ctx.send(f"You said '{ctx.activity.text}'")


if __name__ == "__main__":
    asyncio.run(app.start())
