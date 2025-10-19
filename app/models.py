# app/models.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, date
from decimal import Decimal
from app import db, login_manager

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    role = db.Column(db.String(20), default='user')  # 'admin', 'user'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    transactions = db.relationship('Transaction', backref='user', lazy=True)
    notifications = db.relationship('Notification', backref='user', lazy=True)
    settlements_created = db.relationship('Settlement', foreign_keys='Settlement.created_by', backref='creator', lazy=True)
    settlements_completed = db.relationship('Settlement', foreign_keys='Settlement.completed_by', backref='completer', lazy=True)

class Member(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    outstanding_advance = db.Column(db.Numeric(12,2), default=0)
    color = db.Column(db.String(7), default="#3498db")
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))

class Settlement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    week_start = db.Column(db.Date, nullable=False)
    week_end = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))

    total_income = db.Column(db.Numeric(14,2), nullable=False)
    total_expenses = db.Column(db.Numeric(14,2), nullable=False)
    salary_deduction = db.Column(db.Numeric(14,2), nullable=False)
    debt_deduction = db.Column(db.Numeric(14,2), nullable=False)
    rent_deduction = db.Column(db.Numeric(14,2), nullable=True)
    milk_deduction = db.Column(db.Numeric(14,2), nullable=True)
    total_advances = db.Column(db.Numeric(14,2), nullable=False)
    net_distributable = db.Column(db.Numeric(14,2), nullable=False)
    felix_substitute = db.Column(db.Boolean, default=False)
    
    # Transaction status
    is_completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    completed_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    # Relationships
    settlement_items = db.relationship('SettlementItem', backref='settlement', lazy=True, cascade='all, delete-orphan')

class SettlementItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    settlement_id = db.Column(db.Integer, db.ForeignKey("settlement.id"), nullable=False)
    member_name = db.Column(db.String(80), nullable=False)
    share_ratio = db.Column(db.Numeric(10,4), nullable=False)
    gross_share = db.Column(db.Numeric(14,2), nullable=False)
    advance = db.Column(db.Numeric(14,2), nullable=False)
    net_payout = db.Column(db.Numeric(14,2), nullable=False)
    
    # Transaction tracking
    is_paid = db.Column(db.Boolean, default=False)
    paid_at = db.Column(db.DateTime, nullable=True)
    paid_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    received_at = db.Column(db.DateTime, nullable=True)

class WeeklyAdvance(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    member_name = db.Column(db.String(80), nullable=False)
    amount = db.Column(db.Numeric(14,2), nullable=False)
    advance_date = db.Column(db.Date, nullable=False, default=date.today)
    week_start = db.Column(db.Date, nullable=False)
    week_end = db.Column(db.Date, nullable=False)
    description = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))

class Debt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    total_debt = db.Column(db.Numeric(14,2), default=0)
    remaining_debt = db.Column(db.Numeric(14,2), default=0)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(50), nullable=False)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(45))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
