# config.py
import os
from datetime import timedelta

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key-change-in-production'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///finance.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Email configuration
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.gmail.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = True
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    
    # App constants
    FIXED_SHARES = {
        "Bett": "0.775",
        "Felix": "0.086", 
        "Willy": "0.139",
    }
    FELIX_DAILY_SALARY = "1000.00"
    RENT = "12000.00"
    MILK_BILL = "1500.00"
    DEBT_PERCENT = "0.10"
    
    # JWT settings
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)
