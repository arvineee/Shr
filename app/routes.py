# app/routes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from app import db, mail
from app.models import Settlement, SettlementItem, WeeklyAdvance, Member, Debt, Transaction, Notification, User
from app.utils import quant, week_start_end_for_date, is_last_week_of_month, calculate_settlement_values, get_weekly_advances, send_notification_email
from collections import defaultdict
import json
import calendar

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
@login_required
def index():
    today = date.today()
    monday, sunday = week_start_end_for_date(today)
    members = Member.query.order_by(Member.name).all()
    
    current_advances = get_weekly_advances(monday, sunday)
    advance_history = WeeklyAdvance.query.filter_by(week_start=monday, week_end=sunday)\
                                        .order_by(WeeklyAdvance.created_at.desc()).all()
    
    # Get debt information
    debt = Debt.query.first()
    if not debt:
        debt = Debt(total_debt=0, remaining_debt=0)
        db.session.add(debt)
        db.session.commit()
    
    # Get unread notifications
    notifications = Notification.query.filter_by(user_id=current_user.id, is_read=False)\
                                    .order_by(Notification.created_at.desc()).limit(5).all()
    
    # Get recent settlements for dashboard
    recent_settlements = Settlement.query.order_by(Settlement.created_at.desc()).limit(3).all()
    
    return render_template('index.html',
                         week_start=monday,
                         week_end=sunday,
                         members=members,
                         today=today,
                         current_advances=current_advances,
                         advance_history=advance_history,
                         debt=debt,
                         notifications=notifications,
                         recent_settlements=recent_settlements)

@main_bp.route('/api/add_advance', methods=['POST'])
@login_required
def api_add_advance():
    data = request.get_json()
    member_name = data.get('member_name')
    amount = Decimal(data.get('amount', 0))
    description = data.get('description', '').strip()
    
    today = date.today()
    week_start, week_end = week_start_end_for_date(today)
    
    if amount > 0 and member_name:
        advance = WeeklyAdvance(
            member_name=member_name,
            amount=amount,
            advance_date=today,
            week_start=week_start,
            week_end=week_end,
            description=description,
            created_by=current_user.id
        )
        db.session.add(advance)
        
        # Update member's outstanding advance
        member = Member.query.filter_by(name=member_name).first()
        if member:
            member.outstanding_advance += amount
        
        # Log transaction
        transaction = Transaction(
            user_id=current_user.id,
            action='ADD_ADVANCE',
            details=f'Added KSH {amount} advance for {member_name}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Advance of KSH {amount} added for {member_name}'})
    
    return jsonify({'success': False, 'message': 'Invalid data'})

@main_bp.route('/api/delete_advance/<int:advance_id>', methods=['POST'])
@login_required
def api_delete_advance(advance_id):
    advance = WeeklyAdvance.query.get_or_404(advance_id)
    
    # Update member's outstanding advance
    member = Member.query.filter_by(name=advance.member_name).first()
    if member:
        member.outstanding_advance -= advance.amount
    
    # Log transaction
    transaction = Transaction(
        user_id=current_user.id,
        action='DELETE_ADVANCE',
        details=f'Deleted KSH {advance.amount} advance for {advance.member_name}',
        ip_address=request.remote_addr
    )
    db.session.add(transaction)
    
    db.session.delete(advance)
    db.session.commit()
    
    return jsonify({'success': True, 'message': f'Advance deleted successfully'})

# Enhanced debt logic for routes.py

@main_bp.route('/api/create_settlement', methods=['POST'])
@login_required
def api_create_settlement():
    data = request.get_json()
    
    ref_date_str = data.get('ref_date')
    try:
        ref_date = datetime.strptime(ref_date_str, "%Y-%m-%d").date() if ref_date_str else date.today()
    except Exception:
        ref_date = date.today()
    
    week_start, week_end = week_start_end_for_date(ref_date)
    income = Decimal(data.get('total_income', 0))
    expenses = Decimal(data.get('total_expenses', 0))
    felix_substitute = data.get('felix_substitute', False)

    advances_dict = get_weekly_advances(week_start, week_end)
    calc = calculate_settlement_values(income, expenses, advances_dict, week_start, week_end, felix_substitute)

    # Create settlement
    settlement = Settlement(
        week_start=week_start,
        week_end=week_end,
        total_income=calc["income"],
        total_expenses=calc["expenses"],
        salary_deduction=calc["salary_total"],
        debt_deduction=calc["debt"],
        rent_deduction=calc["rent"] if calc["rent"] > 0 else None,
        milk_deduction=calc["milk"] if calc["milk"] > 0 else None,
        total_advances=calc["total_advances"],
        net_distributable=calc["net_distributable"],
        felix_substitute=felix_substitute,
        created_by=current_user.id
    )
    db.session.add(settlement)
    db.session.flush()

    # Enhanced Debt Management Logic
    debt = Debt.query.first()
    if not debt:
        debt = Debt(total_debt=0, remaining_debt=0)
        db.session.add(debt)
    
    debt_payment = calc["debt"]
    
    if debt.remaining_debt > 0:
        # Apply payment to reduce remaining debt
        if debt_payment >= debt.remaining_debt:
            # Debt is fully paid off
            actual_payment = debt.remaining_debt
            debt.remaining_debt = Decimal("0.00")
            # Optional: You might want to track overpayment
        else:
            # Partial payment
            actual_payment = debt_payment
            debt.remaining_debt -= debt_payment
    else:
        # No existing debt, this becomes new debt
        debt.total_debt += debt_payment
        debt.remaining_debt += debt_payment
        actual_payment = Decimal("0.00")
    
    debt.last_updated = datetime.utcnow()

    # Add settlement items
    for name in calc["gross_shares"].keys():
        item = SettlementItem(
            settlement_id=settlement.id,
            member_name=name,
            share_ratio=Decimal(calc["gross_shares"][name]),
            gross_share=calc["gross_shares"][name],
            advance=quant(advances_dict.get(name, "0")),
            net_payout=calc["net_payouts"][name]
        )
        db.session.add(item)

    # Log transaction with debt payment details
    if debt.remaining_debt == 0 and debt.total_debt > 0:
        debt_status = "Debt fully paid off!"
    elif actual_payment > 0:
        debt_status = f"Debt payment: KSH {actual_payment:.2f}"
    else:
        debt_status = f"New debt added: KSH {debt_payment:.2f}"
    
    transaction = Transaction(
        user_id=current_user.id,
        action='CREATE_SETTLEMENT',
        details=f'Created settlement for week {week_start} to {week_end}. {debt_status}',
        ip_address=request.remote_addr
    )
    db.session.add(transaction)
    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'Settlement created successfully. {debt_status}',
        'settlement_id': settlement.id,
        'debt_status': debt_status
    })

@main_bp.route('/api/mark_received/<int:item_id>', methods=['POST'])
@login_required
def api_mark_received(item_id):
    item = SettlementItem.query.get_or_404(item_id)
    
    if item.net_payout > 0 and not item.is_paid:
        item.received_at = datetime.utcnow()
        
        # Log transaction
        transaction = Transaction(
            user_id=current_user.id,
            action='MARK_RECEIVED',
            details=f'Marked payout received for {item.member_name} - KSH {item.net_payout}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Payment received marked for {item.member_name}'})
    
    return jsonify({'success': False, 'message': 'Invalid operation'})

@main_bp.route('/api/mark_notification_read/<int:notification_id>', methods=['POST'])
@login_required
def api_mark_notification_read(notification_id):
    notification = Notification.query.get_or_404(notification_id)
    
    if notification.user_id == current_user.id:
        notification.is_read = True
        db.session.commit()
        return jsonify({'success': True})
    
    return jsonify({'success': False})

@main_bp.route('/history')
@login_required
def history():
    settlements = Settlement.query.order_by(Settlement.week_start.desc()).all()
    
    # Calculate summary statistics
    total_income = sum(settlement.total_income for settlement in settlements)
    total_net = sum(settlement.net_distributable for settlement in settlements)
    avg_income = total_income / len(settlements) if settlements else 0
    
    summary_stats = {
        'total_income': float(total_income),
        'total_net': float(total_net),
        'avg_income': float(avg_income)
    }
    
    return render_template('history.html', 
                         settlements=settlements, 
                         summary_stats=summary_stats)

@main_bp.route('/settlement/<int:settlement_id>')
@login_required
def settlement_detail(settlement_id):
    settlement = Settlement.query.get_or_404(settlement_id)
    items = SettlementItem.query.filter_by(settlement_id=settlement.id).all()
    members = Member.query.all()
    
    # Get advances used in this settlement
    settlement_advances = WeeklyAdvance.query.filter_by(
        week_start=settlement.week_start, 
        week_end=settlement.week_end
    ).all()
    
    # Get creator user info
    creator = User.query.get(settlement.created_by) if settlement.created_by else None
    
    return render_template('settlement_detail.html', 
                         settlement=settlement, 
                         items=items,
                         settlement_advances=settlement_advances,
                         members=members,
                         creator=creator)

@main_bp.route('/members')
@login_required
def member_management():
    members = Member.query.all()
    total_advances = sum(member.outstanding_advance for member in members)
    
    # Get recent advance activity
    recent_advances = WeeklyAdvance.query.order_by(WeeklyAdvance.created_at.desc()).limit(10).all()
    
    # Get debt information
    debt = Debt.query.first()
    if not debt:
        debt = Debt(total_debt=0, remaining_debt=0)
        db.session.add(debt)
        db.session.commit()
    
    return render_template('members.html', 
                         members=members, 
                         total_advances=total_advances,
                         recent_advances=recent_advances,
                         debt=debt)

@main_bp.route('/api/update_advance/<int:member_id>', methods=['POST'])
@login_required
def api_update_advance(member_id):
    member = Member.query.get_or_404(member_id)
    data = request.get_json()
    new_advance = Decimal(data.get('advance', 0))
    
    try:
        member.outstanding_advance = new_advance
        
        # Log transaction
        transaction = Transaction(
            user_id=current_user.id,
            action='UPDATE_ADVANCE',
            details=f'Updated advance for {member.name} to KSH {new_advance}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Updated advance for {member.name}'})
    except Exception as e:
        return jsonify({'success': False, 'message': 'Error updating advance'})

@main_bp.route('/api/update_member/<int:member_id>', methods=['POST'])
@login_required
def api_update_member(member_id):
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    member = Member.query.get_or_404(member_id)
    data = request.get_json()
    
    try:
        if 'email' in data:
            member.email = data['email']
        if 'phone' in data:
            member.phone = data['phone']
        if 'color' in data:
            member.color = data['color']
        
        # Log transaction
        transaction = Transaction(
            user_id=current_user.id,
            action='UPDATE_MEMBER',
            details=f'Updated member details for {member.name}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Member {member.name} updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': 'Error updating member'})

@main_bp.route('/api/get_settlement_stats')
@login_required
def api_get_settlement_stats():
    settlements = Settlement.query.order_by(Settlement.week_start).all()
    
    if not settlements:
        return jsonify({'success': False, 'message': 'No data available'})
    
    # Prepare data for charts
    weekly_data = []
    for s in settlements:
        weekly_data.append({
            'week': s.week_start.strftime('%Y-%m-%d'),
            'income': float(s.total_income),
            'expenses': float(s.total_expenses),
            'net_distributable': float(s.net_distributable),
            'salary': float(s.salary_deduction),
            'debt': float(s.debt_deduction)
        })
    
    # Calculate member performance
    member_performance = {}
    for member in Member.query.all():
        items = SettlementItem.query.filter_by(member_name=member.name).all()
        total_payout = sum(item.net_payout for item in items)
        total_advances = sum(item.advance for item in items)
        member_performance[member.name] = {
            'total_payout': float(total_payout),
            'total_advances': float(total_advances),
            'net_balance': float(total_payout - total_advances)
        }
    
    return jsonify({
        'success': True,
        'weekly_data': weekly_data,
        'member_performance': member_performance
    })

@main_bp.route('/api/get_current_advances')
@login_required
def api_get_current_advances():
    today = date.today()
    week_start, week_end = week_start_end_for_date(today)
    advances = get_weekly_advances(week_start, week_end)
    return jsonify(advances)

@main_bp.route('/dashboard')
@login_required
def dashboard():
    # Get key statistics
    total_settlements = Settlement.query.count()
    total_members = Member.query.count()
    total_advances = sum(member.outstanding_advance for member in Member.query.all())
    
    # Get debt information
    debt = Debt.query.first()
    if not debt:
        debt = Debt(total_debt=0, remaining_debt=0)
        db.session.add(debt)
        db.session.commit()
    
    # Recent activity
    recent_settlements = Settlement.query.order_by(Settlement.created_at.desc()).limit(5).all()
    recent_advances = WeeklyAdvance.query.order_by(WeeklyAdvance.created_at.desc()).limit(10).all()
    recent_transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(10).all()
    
    # Monthly summary
    current_month = date.today().month
    current_year = date.today().year
    monthly_settlements = Settlement.query.filter(
        db.extract('month', Settlement.week_start) == current_month,
        db.extract('year', Settlement.week_start) == current_year
    ).all()
    
    monthly_income = sum(s.total_income for s in monthly_settlements)
    monthly_net = sum(s.net_distributable for s in monthly_settlements)
    
    return render_template('dashboard.html',
                         total_settlements=total_settlements,
                         total_members=total_members,
                         total_advances=total_advances,
                         debt=debt,
                         recent_settlements=recent_settlements,
                         recent_advances=recent_advances,
                         recent_transactions=recent_transactions,
                         monthly_income=monthly_income,
                         monthly_net=monthly_net)

@main_bp.route('/api/update_debt', methods=['POST'])
@login_required
def api_update_debt():
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Admin access required'})
    
    data = request.get_json()
    new_remaining = Decimal(data.get('remaining_debt', 0))
    
    debt = Debt.query.first()
    if debt:
        debt.remaining_debt = new_remaining
        debt.last_updated = datetime.utcnow()
        
        # Log transaction
        transaction = Transaction(
            user_id=current_user.id,
            action='UPDATE_DEBT',
            details=f'Updated remaining debt to KSH {new_remaining}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Debt updated successfully'})
    
    return jsonify({'success': False, 'message': 'Debt record not found'})

@main_bp.route('/notifications')
@login_required
def notifications():
    user_notifications = Notification.query.filter_by(user_id=current_user.id)\
                                         .order_by(Notification.created_at.desc()).all()
    return render_template('notifications.html', notifications=user_notifications)

@main_bp.route('/api/mark_all_notifications_read', methods=['POST'])
@login_required
def api_mark_all_notifications_read():
    Notification.query.filter_by(user_id=current_user.id, is_read=False)\
                     .update({'is_read': True})
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'All notifications marked as read'})

@main_bp.route('/profile')
@login_required
def profile():
    user_transactions = Transaction.query.filter_by(user_id=current_user.id)\
                                       .order_by(Transaction.created_at.desc()).limit(20).all()
    return render_template('profile.html', transactions=user_transactions)

@main_bp.route('/api/update_profile', methods=['POST'])
@login_required
def api_update_profile():
    data = request.get_json()
    
    try:
        if 'email' in data:
            current_user.email = data['email']
        
        # Log transaction
        transaction = Transaction(
            user_id=current_user.id,
            action='UPDATE_PROFILE',
            details='Updated profile information',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Profile updated successfully'})
    except Exception as e:
        return jsonify({'success': False, 'message': 'Error updating profile'})

@main_bp.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404

@main_bp.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('errors/500.html'), 500

# Add this to routes.py for AJAX support
@main_bp.route('/api/delete_settlement/<int:settlement_id>', methods=['POST'])
@login_required
def api_delete_settlement(settlement_id):
    """API endpoint for AJAX settlement deletion"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': 'Admin access required'})

    try:
        settlement = Settlement.query.get_or_404(settlement_id)

        if settlement.is_completed:
            return jsonify({'success': False, 'message': 'Cannot delete completed settlements'})

        # Store details for logging
        settlement_details = {
            'week_start': settlement.week_start,
            'week_end': settlement.week_end,
            'total_income': settlement.total_income
        }

        # Get related data
        settlement_items = SettlementItem.query.filter_by(settlement_id=settlement_id).all()

        # Reverse debt payment
        debt = Debt.query.first()
        if debt and settlement.debt_deduction > 0:
            debt.remaining_debt += settlement.debt_deduction
            if debt.remaining_debt > debt.total_debt:
                debt.total_debt = debt.remaining_debt

        # Delete items and settlement
        for item in settlement_items:
            db.session.delete(item)

        db.session.delete(settlement)

        # Log transaction
        transaction = Transaction(
            user_id=current_user.id,
            action='DELETE_SETTLEMENT',
            details=f'Deleted settlement for week {settlement_details["week_start"]} to {settlement_details["week_end"]}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Settlement deleted successfully',
            'redirect': url_for('main.history')
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error deleting settlement: {str(e)}'})
