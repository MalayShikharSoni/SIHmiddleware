import os
import requests
import tempfile
import speech_recognition as sr
from pydub import AudioSegment
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import json

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

# Initialize speech recognizer (FREE!)
recognizer = sr.Recognizer()

def download_whatsapp_media(media_id):
    """Download media file from WhatsApp"""
    try:
        # Get media URL
        url = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        
        response = requests.get(url, headers=headers)
        media_info = response.json()
        
        if 'url' not in media_info:
            return None
        
        # Download the actual file
        media_response = requests.get(media_info['url'], headers=headers)
        
        if media_response.status_code == 200:
            return media_response.content
        return None
    except Exception as e:
        print(f"Error downloading media: {e}")
        return None

def convert_voice_to_text(audio_content):
    """Convert voice message to text using FREE Google Speech Recognition"""
    try:
        # Save audio content to temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix='.ogg') as temp_ogg:
            temp_ogg.write(audio_content)
            temp_ogg_path = temp_ogg.name
        
        # Convert OGG to WAV (required for speech_recognition)
        audio = AudioSegment.from_ogg(temp_ogg_path)
        temp_wav_path = temp_ogg_path.replace('.ogg', '.wav')
        audio.export(temp_wav_path, format="wav")
        
        # Use speech recognition
        with sr.AudioFile(temp_wav_path) as source:
            audio_data = recognizer.record(source)
            
            # Try Google Speech Recognition (FREE with limitations)
            try:
                text = recognizer.recognize_google(audio_data)
                return text
            except sr.UnknownValueError:
                print("Could not understand audio")
                return None
            except sr.RequestError as e:
                print(f"Error with speech recognition service: {e}")
                # Fallback to offline recognition if available
                try:
                    text = recognizer.recognize_sphinx(audio_data)
                    return text
                except:
                    return None
        
    except Exception as e:
        print(f"Error converting voice to text: {e}")
        return None
    finally:
        # Clean up temporary files
        try:
            os.unlink(temp_ogg_path)
            if 'temp_wav_path' in locals():
                os.unlink(temp_wav_path)
        except:
            pass

def send_to_botpress(user_id, message_text):
    """Send converted text to Botpress messaging API"""
    try:
        # Use Botpress Messaging API to send user message
        url = f"https://messaging.botpress.cloud/{BOTPRESS_BOT_ID}/conversations/{user_id}/messages"
        
        payload = {
            "type": "text",
            "payload": {
                "text": message_text
            }
        }
        
        headers = {
            "Content-Type": "application/json",
            "x-bot-id": BOTPRESS_BOT_ID,
            "Authorization": f"Bearer {BOTPRESS_TOKEN}"
        }
        
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 200:
            return True
        return False
    except Exception as e:
        print(f"Error sending to Botpress: {e}")
        return False

def send_whatsapp_message(phone_number, message):
    """Send message back to WhatsApp"""
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
        
        response = requests.post(url, json=payload, headers=headers)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending WhatsApp message: {e}")
        return False

def handle_botpress_response(conversation_id, bot_message):
    """Handle response from Botpress and send to WhatsApp"""
    try:
        send_whatsapp_message(conversation_id, bot_message)
        return True
    except Exception as e:
        print(f"Error handling Botpress response: {e}")
        return False

@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        # Webhook verification for WhatsApp
        verify_token = request.args.get('hub.verify_token')
        if verify_token == WHATSAPP_VERIFY_TOKEN:
            return request.args.get('hub.challenge')
        return 'Verification failed', 403
    
    elif request.method == 'POST':
        # Handle incoming messages from WhatsApp
        data = request.get_json()
        
        try:
            # Extract message data
            entry = data['entry'][0]
            changes = entry['changes'][0]
            value = changes['value']
            
            if 'messages' in value:
                message = value['messages'][0]
                phone_number = message['from']
                
                # Check if it's a voice message
                if message['type'] == 'audio':
                    audio_id = message['audio']['id']
                    
                    # Send "processing" message
                    send_whatsapp_message(phone_number, "🎤 Processing your voice message...")
                    
                    # Download audio file
                    audio_content = download_whatsapp_media(audio_id)
                    
                    if audio_content:
                        # Convert to text using FREE speech recognition
                        transcribed_text = convert_voice_to_text(audio_content)
                        
                        if transcribed_text:
                            # Send transcribed text to Botpress
                            success = send_to_botpress(phone_number, transcribed_text)
                            
                            if not success:
                                send_whatsapp_message(phone_number, "Sorry, I'm having trouble processing your message right now. Please try again.")
                        else:
                            send_whatsapp_message(phone_number, "Sorry, I couldn't understand the voice message. Could you please speak more clearly or send a text message?")
                    else:
                        send_whatsapp_message(phone_number, "Sorry, I couldn't download the voice message. Please try again.")
                
                # Handle regular text messages - forward directly to Botpress
                elif message['type'] == 'text':
                    text_content = message['text']['body']
                    send_to_botpress(phone_number, text_content)
        
        except Exception as e:
            print(f"Error processing WhatsApp webhook: {e}")
        
        return jsonify({"status": "success"}), 200

@app.route('/botpress-webhook', methods=['POST'])
def botpress_webhook():
    """Handle responses from Botpress"""
    try:
        data = request.get_json()
        
        # Extract Botpress response data
        conversation_id = data.get('conversationId')
        message_type = data.get('type')
        
        if message_type == 'text':
            bot_message = data.get('payload', {}).get('text', '')
            
            # Send bot response back to WhatsApp
            handle_botpress_response(conversation_id, bot_message)
        
        return jsonify({"status": "success"}), 200
        
    except Exception as e:
        print(f"Error processing Botpress webhook: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)