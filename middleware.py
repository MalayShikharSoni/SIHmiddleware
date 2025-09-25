import os
import requests
import tempfile
import speech_recognition as sr
from pydub import AudioSegment
from flask import Flask, request, jsonify
from dotenv import load_dotenv
import json
from concurrent.futures import ThreadPoolExecutor
import time

# Load environment variables
load_dotenv()

app = Flask(__name__)
executor = ThreadPoolExecutor(max_workers=5)
recognizer = sr.Recognizer()

# Config
WHATSAPP_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN')
WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN')
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
BOTPRESS_WEBHOOK_URL = os.getenv('BOTPRESS_WEBHOOK_URL')
BOTPRESS_BOT_ID = os.getenv('BOTPRESS_BOT_ID')
BOTPRESS_TOKEN = os.getenv('BOTPRESS_TOKEN')

# -------------------
# WhatsApp → Middleware
# -------------------
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        # Webhook verification
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')

        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            print("Webhook verified successfully")
            return challenge
        else:
            print("Webhook verification failed")
            return 'Verification failed', 403

    elif request.method == 'POST':
        data = request.get_json()
        print(f"📩 Received WhatsApp event: {json.dumps(data, indent=2)}")

        # Forward raw payload to Botpress webhook
        try:
            headers = {"Content-Type": "application/json"}
            bp_response = requests.post(BOTPRESS_WEBHOOK_URL, json=data, headers=headers, timeout=5)
            print(f"➡ Forwarded to Botpress, status: {bp_response.status_code}")
        except Exception as e:
            print(f"❌ Error forwarding to Botpress: {e}")

        # Process WhatsApp messages asynchronously
        try:
            for entry in data.get('entry', []):
                for change in entry.get('changes', []):
                    value = change.get('value', {})

                    # Skip delivery/status updates
                    if 'statuses' in value:
                        continue

                    # Process messages
                    for message in value.get('messages', []):
                        phone_number = message['from']
                        msg_type = message['type']

                        if msg_type == 'text':
                            text_content = message['text']['body']
                            executor.submit(send_to_botpress, phone_number, text_content)

                        elif msg_type == 'audio':
                            audio_id = message['audio']['id']
                            send_whatsapp_message(phone_number, "🎤 I received your voice message! Processing...")
                            executor.submit(process_voice_message_async, phone_number, audio_id)

                        else:
                            print(f"Unhandled message type: {msg_type}")

        except Exception as e:
            print(f"❌ Error processing messages: {e}")

        return jsonify({"status": "success"}), 200

# -------------------
# Botpress → Middleware → WhatsApp
# -------------------
@app.route('/botpress-webhook', methods=['POST'])
def botpress_webhook():
    try:
        data = request.get_json()
        print(f"📩 Botpress webhook data: {json.dumps(data, indent=2)}")

        conversation_id = data.get('conversationId')
        message_type = data.get('type')
        bot_message = data.get('payload', {}).get('text', '')

        if message_type == 'text' and conversation_id and bot_message:
            send_whatsapp_message(conversation_id, bot_message)
            print(f"➡ Sent to WhatsApp ({conversation_id}): {bot_message}")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"❌ Error in Botpress webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# -------------------
# Utilities
# -------------------
def send_whatsapp_message(phone_number, message):
    try:
        url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone_number,
            "type": "text",
            "text": {"body": message}
        }
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code == 200:
            return True
        else:
            print(f"❌ WhatsApp send error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending WhatsApp message: {e}")
        return False

def send_to_botpress(user_id, message_text):
    try:
        conversation_url = f"https://messaging.botpress.cloud/{BOTPRESS_BOT_ID}/conversations/{user_id}/messages"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {BOTPRESS_TOKEN}"}
        payload = {"type": "text", "payload": {"text": message_text}}
        response = requests.post(conversation_url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 201]:
            print(f"➡ Sent to Botpress ({user_id}): {message_text}")
            return True
        else:
            print(f"❌ Botpress send error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending to Botpress: {e}")
        return False

def download_whatsapp_media(media_id):
    try:
        url = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        media_info = requests.get(url, headers=headers, timeout=10).json()
        media_response = requests.get(media_info['url'], headers=headers, timeout=15)
        return media_response.content
    except Exception as e:
        print(f"❌ Error downloading media: {e}")
        return None

def convert_voice_to_text(audio_content):
    temp_ogg = tempfile.NamedTemporaryFile(delete=False, suffix='.ogg')
    temp_ogg.write(audio_content)
    temp_ogg.close()
    temp_wav_path = temp_ogg.name.replace('.ogg', '.wav')
    AudioSegment.from_ogg(temp_ogg.name).export(temp_wav_path, format="wav")
    with sr.AudioFile(temp_wav_path) as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        audio_data = recognizer.record(source)
        try:
            return recognizer.recognize_google(audio_data, language='en-US')
        except sr.UnknownValueError:
            return "Sorry, I couldn't understand the audio."
        except sr.RequestError:
            return "Sorry, voice recognition service is unavailable."
        finally:
            os.unlink(temp_ogg.name)
            os.unlink(temp_wav_path)

def process_voice_message_async(phone_number, audio_id):
    audio_content = download_whatsapp_media(audio_id)
    if not audio_content:
        send_whatsapp_message(phone_number, "❌ Could not download your voice message.")
        return
    text = convert_voice_to_text(audio_content)
    send_to_botpress(phone_number, text)

# -------------------
# Health & Test Endpoints
# -------------------
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "timestamp": time.time()}), 200

@app.route('/test', methods=['GET'])
def test_endpoint():
    return jsonify({"message": "Middleware running"}), 200

# -------------------
# Run
# -------------------
if __name__ == '__main__':
    print("🚀 WhatsApp Middleware running...")
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=False)