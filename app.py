import json
import os
from datetime import datetime, timedelta

from flask import Flask, request, session, render_template
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import pandas as pd
import firebase_admin
from firebase_admin import credentials, firestore

load_dotenv()

# To stop
# heroku ps:scale web=0

JOB_ID = None
EMERGENCY_JOB = None

app = Flask(__name__)
app.secret_key = 'secret key'

account_sid = os.environ.get('SID')
auth_token = os.environ.get('TOKEN')
TWILIO_NUMBER = os.environ.get('TWILIO_NUMBER')
client = Client(account_sid, auth_token)

scheduler = BackgroundScheduler(daemon=True)
scheduler.start()

# Initialize Firestore DB
firebase_key = os.environ.get('FIREBASE_KEY', 'sms-emergency-alert-firebase-key.json')
try:
    cred = credentials.Certificate(json.loads(firebase_key))
except json.decoder.JSONDecodeError:
    cred = credentials.Certificate(firebase_key)

firebase_admin.initialize_app(cred)
db = firestore.client()
response_ref = db.collection('response')


# TODO: add logic in questions.json for negative responses (e.g., reply 0 while asked to choose in 1-4)
with open('questions.json') as json_file:
    questions = json.load(json_file)


def check_in(name, to, emergency_number, text):
    global EMERGENCY_JOB
    client.messages.create(
        body=text,
        from_=TWILIO_NUMBER,
        to=to
    )

    # FIXME
    # time_limit = datetime.utcnow() + timedelta(minutes=5)
    time_limit = datetime.utcnow() + timedelta(minutes=1)
    EMERGENCY_JOB = scheduler.add_job(func=emergency_notice,
                                      args=[name, to, emergency_number],
                                      trigger="date",
                                      run_date=time_limit)


def emergency_notice(name, my_number, emergency_number):
    print('emergency!')
    client.messages.create(
        body='Hey, this is ' + name + '. I went out but I might not have made it back safely. '
                                      'Give me a call at ' + my_number + ' . (I used the emergency alert '
                                                                         'app to send this message.)',
        from_=TWILIO_NUMBER,
        to=emergency_number
    )
    client.messages.create(
        body='We have sent your emergency contact an alert message.',
        from_=TWILIO_NUMBER,
        to=my_number
    )


@app.route("/", methods=['GET', 'POST'])
def index():
    return "Emergency Alert is running!"


@app.route("/sms", methods=['GET', 'POST'])
def sms_reply():
    global JOB_ID, EMERGENCY_JOB
    resp = MessagingResponse()

    # TODO: cover edge cases of return after completion (lead to key error -1 for now)
    req_body = request.values.get('Body')

    if req_body == "restart":  # for testing purpose
        session.clear()

    # check if the user has already started a session
    if 'question_id' not in session:
        # if the user doesn't have a session started, start them
        resp_txt = questions["0"]["text"]
        msg = resp.message(resp_txt)
        session['question_id'] = '1'
        session['from_number'] = request.values.get('From')
    else:
        question_id = session['question_id']
        resp_txt = questions[question_id]["text"]
        log_txt = questions[str(int(question_id) - 1)]["text"]

        if question_id == '1':
            session['name'] = req_body
        elif question_id == '2':
            session['emergency_number'] = req_body
        elif question_id == '3':
            # Set reminder for when trip ends
            time_given = pd.to_datetime(req_body, format='%Hh%Mm')
            h = time_given.hour
            m = time_given.minute

            # FIXME
            time_limit = datetime.utcnow() + timedelta(hours=h, minutes=m)
            time_limit = datetime.utcnow() + timedelta(seconds=5)
            JOB_ID = scheduler.add_job(func=check_in,
                                       args=[session['name'], session['from_number'],
                                             session['emergency_number'], resp_txt['check']],
                                       trigger="date",
                                       run_date=time_limit,
                                       id='my_job_id')
            resp_txt = resp_txt['resp']
        elif question_id == '4':
            # Cancel tasks
            if JOB_ID is not None:
                JOB_ID.remove()
                JOB_ID = None
            if EMERGENCY_JOB is not None:
                print('removing emergency job')
                EMERGENCY_JOB.remove()
                EMERGENCY_JOB = None

        # Send a response and log data
        resp.message(resp_txt)
        log_data_firestore(question_id, log_txt, req_body)

        next_id = questions[str(question_id)]["next_question"]
        session['question_id'] = next_id
    return str(resp)


def log_data_firestore(question_id, msg, req_body):
    document_id = request.values.get('From')  # unique identifier of the user

    new_message_entry = response_ref.document(document_id).collection("messages")

    new_message_entry.add({
        u'q_id': question_id,
        u'question': msg,
        u'response': req_body,
        u'time': firestore.SERVER_TIMESTAMP
    })

    new_message_entry = response_ref.document(document_id).set({
        'latest_visit': firestore.SERVER_TIMESTAMP,
        'latest_q_id': question_id
    })


@app.route("/dashboard", methods=['GET'])
def dashboard():
    docs = []
    try:
        for doc in response_ref.stream():
            resp = doc.to_dict()
            resp['id'] = doc.id
            docs.append(resp)
    except Exception as e:
        print(f"An Error Occurred: {e}")
    return render_template('dashboard.html', data=docs)


@app.route("/dashboard/<user_id>", methods=['GET'])
def user_details(user_id):
    messages = response_ref.document(str(user_id)).collection('messages').stream()
    docs = []

    try:
        for doc in messages:
            docs.append(doc.to_dict())
    except Exception as e:
        print(f"An Error Occurred: {e}")
    return render_template('user_details.html', data=docs, user_id=user_id)


if __name__ == "__main__":
    app.run(debug=True)
