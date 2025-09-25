import os
import requests
import tempfile
import speech_recognition as sr
from pydub import AudioSegment
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import json
import threading
from concurrent.futures import ThreadPoolExecutor
import time

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Configuration
WHATSAPP_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN')
WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN')
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
BOTPRESS_WEBHOOK_URL = os.getenv('BOTPRESS_WEBHOOK_URL')
BOTPRESS_BOT_ID = os.getenv('BOTPRESS_BOT_ID')
BOTPRESS_TOKEN = os.getenv('BOTPRESS_TOKEN')

# Initialize speech recognizer
recognizer = sr.Recognizer()
executor = ThreadPoolExecutor(max_workers=5)

def download_whatsapp_media(media_id):
    """Download media file from WhatsApp with timeout"""
    try:
        # Get media URL with timeout
        url = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        media_info = response.json()
        
        if 'url' not in media_info:
            print(f"No URL in media info: {media_info}")
            return None
        
        # Download the actual file with timeout
        media_response = requests.get(media_info['url'], headers=headers, timeout=15)
        media_response.raise_for_status()
        
        return media_response.content
        
    except requests.exceptions.Timeout:
        print("Timeout downloading media")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Request error downloading media: {e}")
        return None
    except Exception as e:
        print(f"Error downloading media: {e}")
        return None

def convert_voice_to_text(audio_content):
    """Convert voice message to text with better error handling"""
    temp_ogg_path = None
    temp_wav_path = None
    
    try:
        # Save audio content to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.ogg') as temp_ogg:
            temp_ogg.write(audio_content)
            temp_ogg_path = temp_ogg.name
        
        # Convert OGG to WAV
        audio = AudioSegment.from_ogg(temp_ogg_path)
        temp_wav_path = temp_ogg_path.replace('.ogg', '.wav')
        audio.export(temp_wav_path, format="wav")
        
        # Use speech recognition
        with sr.AudioFile(temp_wav_path) as source:
            # Adjust for ambient noise
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio_data = recognizer.record(source)
            
            # Try Google Speech Recognition with timeout
            try:
                text = recognizer.recognize_google(audio_data, language='en-US')
                return text
            except sr.UnknownValueError:
                print("Could not understand audio")
                return "Sorry, I couldn't understand the audio clearly."
            except sr.RequestError as e:
                print(f"Error with Google speech recognition: {e}")
                # Fallback to offline recognition
                try:
                    text = recognizer.recognize_sphinx(audio_data)
                    return text
                except:
                    return "Sorry, I'm having trouble with voice recognition right now."
        
    except Exception as e:
        print(f"Error converting voice to text: {e}")
        return "Sorry, I couldn't process the voice message."
    finally:
        # Clean up temporary files
        for file_path in [temp_ogg_path, temp_wav_path]:
            if file_path:
                try:
                    os.unlink(file_path)
                except:
                    pass

def send_to_botpress(user_id, message_text):
    """Send message to Botpress with better error handling"""
    try:
        # Create conversation if it doesn't exist
        conversation_url = f"https://messaging.botpress.cloud/{BOTPRESS_BOT_ID}/conversations/{user_id}"
        
        headers = {
            "Content-Type": "application/json",
            "x-bot-id": BOTPRESS_BOT_ID,
            "Authorization": f"Bearer {BOTPRESS_TOKEN}"
        }
        
        # First, ensure conversation exists
        conv_response = requests.get(conversation_url, headers=headers, timeout=10)
        
        # Send message to conversation
        message_url = f"{conversation_url}/messages"
        payload = {
            "type": "text",
            "payload": {
                "text": message_text
            }
        }
        
        response = requests.post(message_url, json=payload, headers=headers, timeout=10)
        
        if response.status_code in [200, 201]:
            print(f"Successfully sent to Botpress: {message_text}")
            return True
        else:
            print(f"Botpress error: {response.status_code} - {response.text}")
            return False
            
    except requests.exceptions.Timeout:
        print("Timeout sending to Botpress")
        return False
    except Exception as e:
        print(f"Error sending to Botpress: {e}")
        return False

def send_whatsapp_message(phone_number, message):
    """Send message to WhatsApp with better error handling"""
    try:
        url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_number,
            "type": "text",
            "text": {
                "body": message
            }
        }
        
        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        
        if response.status_code == 200:
            print(f"WhatsApp message sent successfully to {phone_number}")
            return True
        else:
            print(f"WhatsApp send error: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"Error sending WhatsApp message: {e}")
        return False

def process_voice_message_async(phone_number, audio_id):
    """Process voice message asynchronously"""
    try:
        print(f"Processing voice message for {phone_number}")
        
        # Download audio file
        audio_content = download_whatsapp_media(audio_id)
        
        if not audio_content:
            send_whatsapp_message(phone_number, "Sorry, I couldn't download your voice message. Please try again.")
            return
        
        print("Audio downloaded successfully")
        
        # Convert to text
        transcribed_text = convert_voice_to_text(audio_content)
        
        if transcribed_text and not transcribed_text.startswith("Sorry"):
            print(f"Transcribed: {transcribed_text}")
            
            # Send transcribed text to Botpress
            success = send_to_botpress(phone_number, transcribed_text)
            
            if not success:
                send_whatsapp_message(phone_number, "I heard: \"" + transcribed_text + "\"\n\nBut I'm having trouble connecting to my brain right now. Please try again in a moment.")
        else:
            send_whatsapp_message(phone_number, transcribed_text or "Sorry, I couldn't understand the voice message.")
            
    except Exception as e:
        print(f"Error in async voice processing: {e}")
        send_whatsapp_message(phone_number, "Sorry, there was an error processing your voice message.")

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        # Webhook verification for WhatsApp
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        print(f"Webhook verification - Mode: {mode}, Token: {token}")
        
        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            print("Webhook verified successfully")
            return challenge
        else:
            print("Webhook verification failed")
            return 'Verification failed', 403
    
    elif request.method == 'POST':
        # Handle incoming messages from WhatsApp
        data = request.get_json()
        print(f"Received webhook data: {json.dumps(data, indent=2)}")
        
        try:
            # Check if there are any entries
            if not data.get('entry'):
                print("No entries in webhook data")
                return jsonify({"status": "success"}), 200
            
            # Process each entry
            for entry in data['entry']:
                if 'changes' not in entry:
                    continue
                    
                for change in entry['changes']:
                    value = change.get('value', {})
                    
                    # Handle status updates (delivery receipts)
                    if 'statuses' in value:
                        print(f"Status update: {value['statuses']}")
                        continue
                    
                    # Handle incoming messages
                    if 'messages' in value:
                        for message in value['messages']:
                            phone_number = message['from']
                            message_type = message['type']
                            
                            print(f"Message from {phone_number}, type: {message_type}")
                            
                            # Handle voice messages
                            if message_type == 'audio':
                                audio_id = message['audio']['id']
                                
                                # Send immediate acknowledgment
                                send_whatsapp_message(phone_number, "🎤 I received your voice message! Processing...")
                                
                                # Process voice message in background
                                executor.submit(process_voice_message_async, phone_number, audio_id)
                            
                            # Handle text messages - forward to Botpress
                            elif message_type == 'text':
                                text_content = message['text']['body']
                                print(f"Text message: {text_content}")
                                
                                # Send to Botpress in background to avoid blocking
                                executor.submit(send_to_botpress, phone_number, text_content)
                            
                            # Handle other message types
                            else:
                                print(f"Unhandled message type: {message_type}")
        
        except Exception as e:
            print(f"Error processing WhatsApp webhook: {e}")
            import traceback
            traceback.print_exc()
        
        # Always return success quickly to WhatsApp
        return jsonify({"status": "success"}), 200

@app.route('/botpress-webhook', methods=['POST'])
def botpress_webhook():
    """Handle responses from Botpress"""
    try:
        data = request.get_json()
        print(f"Botpress webhook data: {json.dumps(data, indent=2)}")
        
        # Extract conversation ID and message
        conversation_id = data.get('conversationId')
        message_type = data.get('type')
        
        if message_type == 'text' and conversation_id:
            bot_message = data.get('payload', {}).get('text', '')
            
            if bot_message:
                # Send bot response back to WhatsApp
                success = send_whatsapp_message(conversation_id, bot_message)
                if success:
                    print(f"Bot response sent to {conversation_id}: {bot_message}")
        
        return jsonify({"status": "success"}), 200
        
    except Exception as e:
        print(f"Error processing Botpress webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "timestamp": time.time(),
        "config": {
            "whatsapp_configured": bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID),
            "botpress_configured": bool(BOTPRESS_BOT_ID and BOTPRESS_TOKEN)
        }
    }), 200

@app.route('/test', methods=['GET'])
def test_endpoint():
    """Test endpoint for debugging"""
    return jsonify({
        "message": "Middleware is running",
        "env_vars": {
            "WHATSAPP_TOKEN": "***" if WHATSAPP_TOKEN else None,
            "WHATSAPP_PHONE_NUMBER_ID": WHATSAPP_PHONE_NUMBER_ID,
            "BOTPRESS_BOT_ID": BOTPRESS_BOT_ID,
            "BOTPRESS_TOKEN": "***" if BOTPRESS_TOKEN else None
        }
    }), 200

if __name__ == '__main__':
    print("Starting WhatsApp Voice-to-Text Middleware...")
    print(f"WhatsApp Phone Number ID: {WHATSAPP_PHONE_NUMBER_ID}")
    print(f"Botpress Bot ID: {BOTPRESS_BOT_ID}")
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)