# app/admin_routes.py
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from app import db, mail
from app.models import User, Member, Settlement, SettlementItem, Transaction, Debt, Notification
from app.utils import send_notification_email
from functools import wraps
from datetime import datetime

admin_bp = Blueprint('admin', __name__)

def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            flash('Admin access required', 'error')
            return redirect(url_for('main.index'))
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route('/dashboard')
@admin_required
def dashboard():
    """Admin dashboard with system statistics"""
    try:
        # Admin statistics
        stats = {
            'total_users': User.query.count(),
            'total_settlements': Settlement.query.count(),
            'total_transactions': Transaction.query.count(),
            'total_members': Member.query.count(),
            'recent_settlements': Settlement.query.order_by(Settlement.created_at.desc()).limit(5).all()
        }
        
        # Debt information
        debt = Debt.query.first()
        if not debt:
            debt = Debt(total_debt=0, remaining_debt=0)
            db.session.add(debt)
            db.session.commit()
        
        # Recent activity
        recent_transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(10).all()
        
        return render_template('admin/dashboard.html', 
                             stats=stats, 
                             debt=debt,
                             recent_transactions=recent_transactions)
    except Exception as e:
        flash(f'Error loading dashboard: {str(e)}', 'error')
        return redirect(url_for('main.index'))

@admin_bp.route('/complete_transaction/<int:settlement_id>', methods=['POST'])
@admin_required
def complete_transaction(settlement_id):
    """Mark settlement as completed and send notifications"""
    try:
        settlement = Settlement.query.get_or_404(settlement_id)
        
        if not settlement.is_completed:
            settlement.is_completed = True
            settlement.completed_at = datetime.utcnow()
            settlement.completed_by = current_user.id
            
            # Get settlement items using direct query (temporary fix until relationship is established)
            settlement_items = SettlementItem.query.filter_by(settlement_id=settlement.id).all()
            
            # Mark all items as paid
            for item in settlement_items:
                item.is_paid = True
                item.paid_at = datetime.utcnow()
                item.paid_by = current_user.id
                
                # Create notification for member
                member = Member.query.filter_by(name=item.member_name).first()
                if member:
                    notification = Notification(
                        user_id=current_user.id,
                        title=f'Payment Processed - KSH {item.net_payout:.2f}',
                        message=f'Your payment of KSH {item.net_payout:.2f} has been processed for the week {settlement.week_start} to {settlement.week_end}.',
                        is_read=False
                    )
                    db.session.add(notification)
                    
                    # Send email notification if member has email
                    if member.email:
                        try:
                            send_notification_email(
                                member.email,
                                'Payment Processed',
                                'payment_processed.html',
                                member=member,
                                settlement=settlement,
                                item=item
                            )
                        except Exception as email_error:
                            print(f"Email sending failed: {email_error}")
                            # Continue even if email fails
            
            # Log transaction
            transaction = Transaction(
                user_id=current_user.id,
                action='COMPLETE_TRANSACTION',
                details=f'Completed transaction for settlement {settlement_id}',
                ip_address=request.remote_addr
            )
            db.session.add(transaction)
            db.session.commit()
            
            flash('Transaction completed and notifications sent', 'success')
        else:
            flash('Transaction was already completed', 'info')
    
    except Exception as e:
        db.session.rollback()
        flash(f'Error completing transaction: {str(e)}', 'error')
    
    return redirect(url_for('main.settlement_detail', settlement_id=settlement_id))

@admin_bp.route('/manage_users')
@admin_required
def manage_users():
    """Manage system users"""
    try:
        users = User.query.order_by(User.username).all()
        return render_template('admin/manage_users.html', users=users)
    except Exception as e:
        flash(f'Error loading users: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

@admin_bp.route('/system_logs')
@admin_required
def system_logs():
    """View system transaction logs"""
    try:
        transactions = Transaction.query.order_by(Transaction.created_at.desc()).limit(100).all()
        return render_template('admin/system_logs.html', transactions=transactions)
    except Exception as e:
        flash(f'Error loading system logs: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

@admin_bp.route('/notifications')
@admin_required
def admin_notifications():
    """Admin view of all system notifications"""
    try:
        # Get all notifications across all users
        all_notifications = Notification.query.order_by(Notification.created_at.desc()).all()
        
        # Get all active users for the send notification form
        active_users = User.query.filter_by(is_active=True).order_by(User.username).all()

        # Group by user for better organization
        notifications_by_user = {}
        for notification in all_notifications:
            user = User.query.get(notification.user_id)
            if user:
                if user.id not in notifications_by_user:
                    notifications_by_user[user.id] = {
                        'user': user,
                        'notifications': []
                    }
                notifications_by_user[user.id]['notifications'].append(notification)
        
        # Calculate statistics
        unread_count = sum(1 for notification in all_notifications if not notification.is_read)
        today_count = sum(1 for notification in all_notifications 
                         if notification.created_at.date() == datetime.utcnow().date())

        return render_template('admin/notifications.html',
                             notifications_by_user=notifications_by_user,
                             active_users=active_users,
                             total_notifications=len(all_notifications),
                             unread_count=unread_count,
                             today_count=today_count)
    except Exception as e:
        flash(f'Error loading notifications: {str(e)}', 'error')
        return redirect(url_for('admin.dashboard'))

@admin_bp.route('/api/update_user/<int:user_id>', methods=['POST'])
@admin_required
def api_update_user(user_id):
    """Update user information"""
    if user_id == current_user.id:
        return jsonify({'success': False, 'message': 'Cannot edit your own account'})
    
    user = User.query.get_or_404(user_id)
    data = request.get_json()
    
    try:
        # Validate input
        if 'email' in data and data['email']:
            # Check if email is already taken by another user
            existing_user = User.query.filter(User.email == data['email'], User.id != user_id).first()
            if existing_user:
                return jsonify({'success': False, 'message': 'Email already taken by another user'})
            user.email = data['email']
        
        if 'role' in data and data['role'] in ['admin', 'user']:
            user.role = data['role']
        
        if 'is_active' in data:
            user.is_active = bool(data['is_active'])
        
        # Log the action
        transaction = Transaction(
            user_id=current_user.id,
            action='UPDATE_USER',
            details=f'Updated user {user.username}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'User {user.username} updated successfully'})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error updating user: {str(e)}'})

@admin_bp.route('/api/toggle_user/<int:user_id>', methods=['POST'])
@admin_required
def api_toggle_user(user_id):
    """Toggle user active status"""
    if user_id == current_user.id:
        return jsonify({'success': False, 'message': 'Cannot deactivate your own account'})
    
    user = User.query.get_or_404(user_id)
    
    try:
        user.is_active = not user.is_active
        
        # Log the action
        transaction = Transaction(
            user_id=current_user.id,
            action='TOGGLE_USER_STATUS',
            details=f'{"Activated" if user.is_active else "Deactivated"} user {user.username}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        action = 'activated' if user.is_active else 'deactivated'
        return jsonify({'success': True, 'message': f'User {user.username} {action} successfully'})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error updating user status: {str(e)}'})

@admin_bp.route('/api/send_notification', methods=['POST'])
@admin_required
def api_send_notification():
    """Send notification to specific users or all users"""
    data = request.get_json()
    
    title = data.get('title', '').strip()
    message = data.get('message', '').strip()
    target_users = data.get('target_users', 'all')
    specific_user_ids = data.get('specific_user_ids', [])
    
    if not title or not message:
        return jsonify({'success': False, 'message': 'Title and message are required'})
    
    try:
        users_to_notify = []
        
        if target_users == 'all':
            users_to_notify = User.query.filter_by(is_active=True).all()
        else:
            # Send to specific users
            for user_id in specific_user_ids:
                user = User.query.get(int(user_id))
                if user and user.is_active:
                    users_to_notify.append(user)
        
        # Create notifications for each user
        notification_count = 0
        for user in users_to_notify:
            notification = Notification(
                user_id=user.id,
                title=title,
                message=message,
                is_read=False
            )
            db.session.add(notification)
            notification_count += 1
            
            # Send email notification if user has email
            if user.email:
                try:
                    send_notification_email(
                        user.email,
                        title,
                        'admin_notification.html',
                        user=user,
                        message=message,
                        title=title
                    )
                except Exception as email_error:
                    print(f"Email sending failed for {user.email}: {email_error}")
                    # Continue even if email fails
        
        # Log the action
        transaction = Transaction(
            user_id=current_user.id,
            action='SEND_NOTIFICATION',
            details=f'Sent notification to {notification_count} users: {title}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'Notification sent to {notification_count} users'
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error sending notification: {str(e)}'})

@admin_bp.route('/api/delete_notification/<int:notification_id>', methods=['POST'])
@admin_required
def api_delete_notification(notification_id):
    """Delete a notification (admin only)"""
    notification = Notification.query.get_or_404(notification_id)
    
    try:
        # Log the action before deletion
        transaction = Transaction(
            user_id=current_user.id,
            action='DELETE_NOTIFICATION',
            details=f'Deleted notification: {notification.title}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        
        db.session.delete(notification)
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Notification deleted successfully'})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error deleting notification: {str(e)}'})

@admin_bp.route('/api/bulk_delete_notifications', methods=['POST'])
@admin_required
def api_bulk_delete_notifications():
    """Bulk delete notifications"""
    data = request.get_json()
    notification_ids = data.get('notification_ids', [])
    
    if not notification_ids:
        return jsonify({'success': False, 'message': 'No notifications selected'})
    
    try:
        # Convert to integers and validate
        notification_ids = [int(nid) for nid in notification_ids]
        
        # Get notifications to log details
        notifications = Notification.query.filter(Notification.id.in_(notification_ids)).all()
        
        # Delete notifications
        deleted_count = Notification.query.filter(Notification.id.in_(notification_ids)).delete(synchronize_session=False)
        
        # Log the action
        transaction = Transaction(
            user_id=current_user.id,
            action='BULK_DELETE_NOTIFICATIONS',
            details=f'Deleted {deleted_count} notifications',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'Deleted {deleted_count} notifications'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error deleting notifications: {str(e)}'})

@admin_bp.route('/api/create_user', methods=['POST'])
@admin_required
def api_create_user():
    """Create a new user (admin only)"""
    data = request.get_json()
    
    username = data.get('username', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'user')
    
    if not username or not email or not password:
        return jsonify({'success': False, 'message': 'Username, email, and password are required'})
    
    try:
        # Check if username or email already exists
        if User.query.filter_by(username=username).first():
            return jsonify({'success': False, 'message': 'Username already exists'})
        
        if User.query.filter_by(email=email).first():
            return jsonify({'success': False, 'message': 'Email already exists'})
        
        # Create user (in a real app, you'd hash the password)
        from werkzeug.security import generate_password_hash
        user = User(
            username=username,
            email=email,
            password_hash=generate_password_hash(password),
            role=role,
            is_active=True
        )
        db.session.add(user)
        
        # Log the action
        transaction = Transaction(
            user_id=current_user.id,
            action='CREATE_USER',
            details=f'Created user {username} with role {role}',
            ip_address=request.remote_addr
        )
        db.session.add(transaction)
        db.session.commit()
        
        return jsonify({'success': True, 'message': f'User {username} created successfully'})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'Error creating user: {str(e)}'})
