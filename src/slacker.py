import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from datetime import datetime, timedelta
from anthropic import Anthropic
import json

class SlackerBot:
    def __init__(self, slack_token, anthropic_api_key):
        """Initialise with Anthropic and Slack API keys"""
        self.slack_client = WebClient(token=slack_token)
        self.anthropic_client = Anthropic(api_key=anthropic_api_key)
        
    def fetch_messages(self, channel_id, start_time, end_time):
        """Fetch messages from Slack channel within a specified timeframe."""
        try:
            # Convert timestamps to Unix timestamps
            start_ts = start_time.timestamp()
            end_ts = end_time.timestamp()
            
			# Load message list
            messages = []
            result = self.slack_client.conversations_history(
                channel=channel_id,
                oldest=start_ts,
                latest=end_ts,
                limit=100  
            )
            messages.extend(result["messages"])
            
            # Handle pagination 
            while result.get("has_more", False):
                result = self.slack_client.conversations_history(
                    channel=channel_id,
                    oldest=start_ts,
                    latest=end_ts,
                    cursor=result["response_metadata"]["next_cursor"],
                    limit=100
                )
                messages.extend(result["messages"])
            
			# Return message list
            return messages
        
		# Handle API error
        except SlackApiError as e:
            print(f"Error fetching messages: {e.response['error']}")
            return []

    def generate_summary(self, messages):
        """Generate summary using Claude."""
        if not messages:
            return "No messages found in the specified timeframe."
            
        formatted_messages = []
        for msg in messages:
            timestamp = datetime.fromtimestamp(float(msg["ts"]))
            formatted_messages.append(f"{timestamp}: {msg.get('text', '')}")
        
        messages_text = "\n".join(formatted_messages)

        # print("---> Formatted messages:\n", messages_text)
        
        prompt = f"""

            Here are a set of Slack messages from a conversation. 
            I would like you to provide a digest of these messages for participants in this conversion. 
            Please:
            1. Introduce yourself; 
            2. Provide a concise summary of the key points discussed;
            2. Extract specific action items, including who is responsible if mentioned;
            3. Note any important decisions made.

            Please be polite, upbeat, and encouraging. Please use emojis!

            Messages:
            {messages_text}

            Please format your response as JSON with the following structure:
            {{
                "summary": "Overall summary here",
                "action_items": ["Action 1", "Action 2", ...],
                "decisions": ["Decision 1", "Decision 2", ...]
            }}
        """

        try:
            response = self.anthropic_client.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Extract JSON from Claude's response
            try:
                # The response content might contain explanation text before or after the JSON
                # We need to find and extract just the JSON part
                response_text = response.content[0].text
                
                # print("---> Generated response text:\n", response_text)
                
                # Find the JSON structure in the response
                start_idx = response_text.find('{')
                end_idx = response_text.rfind('}') + 1
                
                if start_idx != -1 and end_idx != 0:
                    json_str = response_text[start_idx:end_idx]
                    summary_data = json.loads(json_str)
                    return summary_data
                else:
                    # Fallback structure if JSON parsing fails
                    print("JSON parsing failed, falling back...")
                    return {
                        "summary": response_text,
                        "action_items": [],
                        "decisions": []
                    }
                    
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON from Claude's response: {e}")
                # Fallback structure
                return {
                    "summary": response_text,
                    "action_items": [],
                    "decisions": []
                }
            
        except Exception as e:
            print(f"Error generating summary: {e}")
            return None

    def save_to_file(self, messages, summary, filename):
        """Save messages and summary to a text file."""
        try:
            with open(filename, 'w') as f:
                # Write original messages
                f.write("=== Original Messages ===\n\n")
                for msg in messages:
                    timestamp = datetime.fromtimestamp(float(msg["ts"]))
                    f.write(f"{timestamp}: {msg.get('text', '')}\n")
                
                f.write("\n=== Summary ===\n\n")
                f.write(json.dumps(summary, indent=2))
                
            return True
        except Exception as e:
            print(f"Error saving to file: {e}")
            return False

    def format_slack_message(self, summary_data, start_time, end_time):
        """Format summary data into Slack blocks format."""
        # Create time range string
        time_range = f"{start_time.strftime('%B %d, %Y %H:%M')} to {end_time.strftime('%B %d, %Y %H:%M')}"
        
        # Start with header blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📋 Slack Digest: {time_range}"
                }
            },
            {
                "type": "divider"
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Summary:*\n" + summary_data["summary"]
                }
            }
        ]
        
        # Add action items if present
        if summary_data["action_items"]:
            action_items = "\n".join([f"• {item}" for item in summary_data["action_items"]])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Action Items:*\n{action_items}"
                }
            })
        
        # Add decisions if present
        if summary_data["decisions"]:
            decisions = "\n".join([f"• {item}" for item in summary_data["decisions"]])
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Key Decisions:*\n{decisions}"
                }
            })
            
        return blocks

    def post_to_slack(self, channel_id, blocks):
        """Post formatted blocks to Slack channel."""
        try:
            response = self.slack_client.chat_postMessage(
                channel=channel_id,
                blocks=blocks
            )
            return True
            
        except SlackApiError as e:
            print(f"Error posting to Slack: {e.response['error']}")
            return False

    def process_channel(self, channel_id, start_time, end_time, output_file, post_to_slack=False):
        """Process a channel's messages and generate summary."""
        # Fetch messages
        messages = self.fetch_messages(channel_id, start_time, end_time)

        # Generate summary
        summary = self.generate_summary(messages)
        
        success = False
        if summary:
            # Save to file
            file_success = self.save_to_file(messages, summary, output_file)

            print("---> Summary:\n", summary)
            
            # Post to Slack if requested
            slack_success = True
            if post_to_slack:
                # Format message blocks
                blocks = self.format_slack_message(summary, start_time, end_time)
                # Post to Slack
                slack_success = self.post_to_slack(channel_id, blocks)
            
            success = file_success and slack_success
            
        return success

def test_formatting(bot):
    """Test message formatting."""
    test_summary = {
        "summary": "Test summary",
        "action_items": ["Item 1", "Item 2"],
        "decisions": ["Decision 1"]
    }
    start_time = datetime.now() - timedelta(hours=1)
    end_time = datetime.now()
    
    blocks = bot.format_slack_message(test_summary, start_time, end_time)
    print(json.dumps(blocks, indent=2))

def test_posting(bot, channel_id):
    """Test Slack posting."""
    test_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Test message"
            }
        }
    ]
    success = bot.post_to_slack(f"{channel_id}", test_blocks)
    print(f"Posting {'succeeded' if success else 'failed'}")

def run(bot, post_to_slack=False):
    """Test function."""
    # Setup
    channel_id = "C07DXJHBVR9" # llm_tests
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=(12*4*7*24)) # The past year
    output_file = f"../output/slack_summary_{end_time.strftime('%Y%m%d_%H%M')}.txt"

    # test_formatting(bot)
    # test_posting(bot, channel_id)

    # # Run with Slack posting enabled
    success = bot.process_channel(
        channel_id, 
        start_time, 
        end_time, 
        output_file,
        post_to_slack=post_to_slack  # Enable Slack posting
    )
    
    if success:
        success_str = f"---> Success! Saved to {output_file}"
        if post_to_slack: 
            success_str += " and posted to Slack"
        print(success_str) 
    else:
        print("Error processing channel!")
    
def main():
    """Slacker main."""
    # Get environment variables for API keys
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
    
    # Initialise bot
    bot = SlackerBot(slack_token, anthropic_api_key)
    
    # Test bot
    run(bot, post_to_slack=True)
    
if __name__ == "__main__":
    main()