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

app = Flask(__name__)
app.secret_key = 'secret key'

account_sid = os.environ.get('SID')
auth_token = os.environ.get('TOKEN')
client = Client(account_sid, auth_token)
from_number = os.environ.get('FROM')  # put your twilio number here'
to_number = os.environ.get('TO')  # put your own phone number here

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


def emergency_check(emergency_number):
    print('here')
    client.messages.create(
        body='new text',
        from_=from_number,
        to=emergency_number
    )


@app.route("/", methods=['GET', 'POST'])
def index():
    return "Emergency Alert is running!"


@app.route("/sms", methods=['GET', 'POST'])
def sms_reply():
    global JOB_ID
    resp = MessagingResponse()

    ### TODO: cover edge cases of return after completion (lead to key error -1 for now)
    req_body = request.values.get('Body')

    if req_body == "restart":  # for testing purpose
        session.clear()
    # check if the user has already started a session
    if 'question_id' not in session:
        # if the user doesn't have a session started, start them
        resp_txt = questions["0"]["text"]
        msg = resp.message(resp_txt)
        session['question_id'] = '1'
        log_data_firestore('0', resp_txt, req_body)
    else:
        question_id = session['question_id']

        # TODO: If question is 3 then set reminder + 5 min
        if int(question_id) == 3:
            time_given = pd.to_datetime(req_body, format='%Hh%Mm')
            h = time_given.hour
            m = time_given.minute
            # time_limit = datetime.utcnow() + timedelta(hours=h, minutes=m)
            time_limit = datetime.utcnow() + timedelta(seconds=5)
            JOB_ID = scheduler.add_job(func=emergency_check, args=[to_number], trigger="date", run_date=time_limit, id='my_job_id')
            print('added')
            print(datetime.utcnow())
            print(time_limit)
            scheduler.print_jobs()

        # TODO: If question is 4 then cancel task
        if int(question_id) == 4:
            print('before')
            scheduler.print_jobs()
            # scheduler.remove_job('my_job_id')
            JOB_ID.remove()
            print('after')
            scheduler.print_jobs()
            print('task removed')

        # TODO: If reminder goes off then send emergency alert


        # Send a response and log data
        resp_txt = questions[question_id]["text"]
        msg = resp.message(resp_txt)
        log_data_firestore(question_id, resp_txt, req_body)

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
