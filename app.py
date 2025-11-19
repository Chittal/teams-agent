import asyncio
import re
import uuid
import logging
import json

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

@app.on_message_pattern(re.compile(r"ogp"))
async def open_graph_metatags(ctx: ActivityContext[MessageActivity]) -> None:
    """Handle open graph metatags."""
    await ctx.send("Test open graph metatags: https://ogp.me/")

@app.on_message_pattern(re.compile(r"just url"))
async def open_graph_metatags_url(ctx: ActivityContext[MessageActivity]) -> None:
    """Handle open graph metatags."""
    await ctx.send("https://ogp.me/")

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


@app.on_message
async def handle_message(ctx: ActivityContext[MessageActivity]):
    """Handle message activities using Groq LLM."""
    await ctx.reply(TypingActivityInput())
    logger.debug(f"Received message: {ctx.activity}")
    logger.debug(f"Received message: {json.dumps(ctx.activity.__dict__, default=str, indent=2)}")

    user_message = ctx.activity.text
    
    try:
        # Run Groq LLM in executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, groq_llm.invoke, user_message)
        answer = response.content if hasattr(response, 'content') else str(response)
        
        # Send the AI response
        await ctx.send(answer)
    except Exception as e:
        # Handle errors gracefully
        error_message = f"I encountered an error: {str(e)}. Please try again."
        await ctx.send(error_message)

    # if "reply" in ctx.activity.text.lower():
    #     await ctx.reply("Hello! How can I assist you today?")
    # else:
    #     await ctx.send(f"You said '{ctx.activity.text}'")


if __name__ == "__main__":
    asyncio.run(app.start())
