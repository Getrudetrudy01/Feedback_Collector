# Feedback Collector

A serverless feedback collection system built with AWS Lambda, DynamoDB, and vanilla JavaScript. This application allows users to submit feedback through a web form and provides an admin dashboard to view and manage all submitted feedback.

## Live Demo

- **Feedback Form**: http://my-feedback-collector.s3-website-us-east-1.amazonaws.com/
- **Admin Dashboard**: https://my-feedback-collector.s3.us-east-1.amazonaws.com/admin.html

## Features

- **User Feedback Form**: Clean, responsive form for collecting user feedback
- **Admin Dashboard**: Real-time dashboard to view, monitor, and export feedback
- **Serverless Architecture**: Built on AWS Lambda and DynamoDB for scalability
- **CSV Export**: Export all feedback data to CSV format
- **Real-time Stats**: View total feedback count and average ratings
- **Auto-refresh**: Dashboard automatically refreshes every 30 seconds

## Project Structure

```
feedback_collector/
backend/
lambda_function.py       # AWS Lambda function for handling feedback
frontend/
index.html              # User feedback form
admin.html              # Admin dashboard
app.js                  # Frontend logic for feedback form
style.css               # Styles for the feedback form
 README.md                   # Project documentation
```


