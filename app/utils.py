# app/utils.py
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, date, timedelta
import calendar
from collections import defaultdict
from app import mail
from flask_mail import Message
from flask import render_template, current_app

def quant(v):
    """Quantize decimal values to 2 decimal places with proper rounding"""
    return Decimal(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def week_start_end_for_date(ref_date: date):
    """Return (week_start_date (Mon), week_end_date (Sun)) for a date."""
    weekday = ref_date.weekday()
    monday = ref_date - timedelta(days=weekday)
    sunday = monday + timedelta(days=6)
    return monday, sunday

def is_last_week_of_month(week_start: date, week_end: date):
    """True if the given week (Mon-Sun) contains the last calendar day of the month."""
    last_day = date(week_start.year, week_start.month, 
                   calendar.monthrange(week_start.year, week_start.month)[1])
    return week_start <= last_day <= week_end

def get_weekly_advances(week_start: date, week_end: date):
    """Get all advances for a specific week."""
    from app.models import WeeklyAdvance
    advances = WeeklyAdvance.query.filter_by(week_start=week_start, week_end=week_end).all()
    advances_dict = defaultdict(Decimal)
    for advance in advances:
        advances_dict[advance.member_name] += Decimal(advance.amount)
    return dict(advances_dict)

# In app/utils.py - Update the calculate_settlement_values function

def calculate_settlement_values(total_income, total_expenses, advances_dict, week_start, week_end, felix_substitute=False):
    """
    Enhanced business rules with advance redistribution and substitute logic:
    - If Felix substitute is True: Felix gets shares but NO salary
    - Advances are first deducted from individual shares
    - Any negative payouts (over-advance) are redistributed to other members proportionally
    """
    # Use current_app to access config in application context
    FIXED_SHARES = {k: Decimal(v) for k, v in current_app.config['FIXED_SHARES'].items()}
    FELIX_DAILY_SALARY = Decimal(current_app.config['FELIX_DAILY_SALARY'])
    WEEK_DAYS = 7
    RENT = Decimal(current_app.config['RENT'])
    MILK_BILL = Decimal(current_app.config['MILK_BILL'])
    DEBT_PERCENT = Decimal(current_app.config['DEBT_PERCENT'])
    
    income = quant(total_income)
    expenses = quant(total_expenses)
    
    # FIXED: Felix salary logic - only paid if NO substitute
    salary_total = Decimal("0.00")
    if not felix_substitute:
        salary_total = quant(FELIX_DAILY_SALARY * WEEK_DAYS)
    
    debt = quant(income * DEBT_PERCENT)

    rent = Decimal("0.00")
    milk = Decimal("0.00")
    if is_last_week_of_month(week_start, week_end):
        rent = RENT
        milk = MILK_BILL

    total_advances = quant(sum(Decimal(ad) for ad in advances_dict.values()))

    # Calculate net distributable
    net = income - (expenses + salary_total + debt + rent + milk)
    net_distributable = quant(max(net, Decimal("0.00")))

    # First pass: calculate gross shares and subtract advances
    initial_payouts = {}
    over_advance_members = {}
    total_over_advance = Decimal("0.00")
    
    for name, ratio in FIXED_SHARES.items():
        gross = quant(net_distributable * ratio)
        advance = quant(advances_dict.get(name, 0))
        net_payout = gross - advance
        
        if net_payout < 0:
            # Member took more advance than their share
            over_advance_members[name] = -net_payout  # Store the excess
            total_over_advance += -net_payout
            initial_payouts[name] = Decimal("0.00")  # They get nothing this week
        else:
            initial_payouts[name] = net_payout

    # Redistribute the over-advance amounts to other members
    if total_over_advance > 0:
        # Calculate total share ratio of members who didn't over-advance
        valid_share_total = Decimal("0.00")
        for name in FIXED_SHARES.keys():
            if name not in over_advance_members:
                valid_share_total += FIXED_SHARES[name]
        
        # Redistribute proportional to shares
        for name in initial_payouts.keys():
            if name not in over_advance_members:
                redistribution_ratio = FIXED_SHARES[name] / valid_share_total
                additional_share = quant(total_over_advance * redistribution_ratio)
                initial_payouts[name] += additional_share

    # FIXED: Apply Felix's salary - ONLY if no substitute
    # When substitute is True, Felix only gets shares, NO salary
    final_payouts = {}
    for name, payout in initial_payouts.items():
        if name == "Felix" and not felix_substitute:
            # Felix gets salary + his share payout only when NO substitute
            final_payouts[name] = payout + salary_total
        else:
            # All other members (including Felix with substitute) only get share payout
            final_payouts[name] = payout

    # Return gross shares before advance deduction for display
    gross_shares = {}
    for name, ratio in FIXED_SHARES.items():
        gross_shares[name] = quant(net_distributable * ratio)

    return {
        "income": income,
        "expenses": expenses,
        "salary_total": salary_total,
        "debt": debt,
        "rent": rent,
        "milk": milk,
        "total_advances": total_advances,
        "net_distributable": net_distributable,
        "gross_shares": gross_shares,
        "net_payouts": final_payouts,
        "week_start": week_start,
        "week_end": week_end,
        "felix_substitute": felix_substitute
    }

def generate_chart_data():
    """Generate data for charts using settlements data"""
    from app.models import Settlement
    settlements = Settlement.query.order_by(Settlement.week_start).all()
    
    if not settlements:
        return None
    
    # Prepare data for charts
    data = []
    for s in settlements:
        week_label = f"{s.week_start.strftime('%Y-%m-%d')}"
        data.append({
            'week': week_label,
            'income': float(s.total_income),
            'expenses': float(s.total_expenses),
            'net_distributable': float(s.net_distributable),
            'salary': float(s.salary_deduction),
            'debt': float(s.debt_deduction)
        })
    
    # Calculate summary statistics
    total_income = sum(float(s.total_income) for s in settlements)
    total_net = sum(float(s.net_distributable) for s in settlements)
    avg_income = total_income / len(settlements) if settlements else 0
    
    return {
        'weekly_trend': data,
        'summary_stats': {
            'total_income': total_income,
            'avg_income': avg_income,
            'total_net': total_net
        }
    }

def send_notification_email(to_email, subject, template, **kwargs):
    """Send email notification to users"""
    try:
        msg = Message(
            subject=subject,
            sender=current_app.config['MAIL_USERNAME'],
            recipients=[to_email]
        )
        msg.html = render_template(f'emails/{template}', **kwargs)
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def format_currency(amount):
    """Format amount as Kenyan Shillings"""
    return f"KSH {quant(amount):,.2f}"

def calculate_member_statistics(member_name):
    """Calculate comprehensive statistics for a member"""
    from app.models import SettlementItem, WeeklyAdvance
    
    # Get all settlement items for the member
    items = SettlementItem.query.filter_by(member_name=member_name).all()
    
    # Get all advances for the member
    advances = WeeklyAdvance.query.filter_by(member_name=member_name).all()
    
    total_payout = sum(item.net_payout for item in items)
    total_advances = sum(advance.amount for advance in advances)
    total_gross_share = sum(item.gross_share for item in items)
    
    return {
        'total_payout': total_payout,
        'total_advances': total_advances,
        'total_gross_share': total_gross_share,
        'net_balance': total_payout - total_advances,
        'settlement_count': len(items),
        'advance_count': len(advances)
    }

def get_financial_summary():
    """Get comprehensive financial summary"""
    from app.models import Settlement, Member, Debt
    
    settlements = Settlement.query.all()
    members = Member.query.all()
    debt = Debt.query.first()
    
    if not debt:
        debt = Debt(total_debt=0, remaining_debt=0)
    
    total_income = sum(s.total_income for s in settlements)
    total_expenses = sum(s.total_expenses for s in settlements)
    total_net = sum(s.net_distributable for s in settlements)
    total_advances = sum(m.outstanding_advance for m in members)
    
    # Monthly breakdown
    current_year = date.today().year
    monthly_data = {}
    for month in range(1, 13):
        monthly_settlements = [s for s in settlements if s.week_start.year == current_year and s.week_start.month == month]
        if monthly_settlements:
            monthly_data[calendar.month_name[month]] = {
                'income': sum(s.total_income for s in monthly_settlements),
                'net': sum(s.net_distributable for s in monthly_settlements),
                'count': len(monthly_settlements)
            }
    
    return {
        'total_income': total_income,
        'total_expenses': total_expenses,
        'total_net': total_net,
        'total_advances': total_advances,
        'total_debt': debt.total_debt,
        'remaining_debt': debt.remaining_debt,
        'paid_debt': debt.total_debt - debt.remaining_debt,
        'monthly_data': monthly_data,
        'settlement_count': len(settlements)
    }

def validate_settlement_data(income, expenses, ref_date):
    """Validate settlement data before processing"""
    errors = []
    
    if income <= 0:
        errors.append("Income must be greater than 0")
    
    if expenses < 0:
        errors.append("Expenses cannot be negative")
    
    if income < expenses:
        errors.append("Income cannot be less than expenses")
    
    try:
        week_start, week_end = week_start_end_for_date(ref_date)
        # Check if settlement already exists for this week
        from app.models import Settlement
        existing_settlement = Settlement.query.filter_by(week_start=week_start, week_end=week_end).first()
        if existing_settlement:
            errors.append("A settlement already exists for this week")
    except Exception:
        errors.append("Invalid date format")
    
    return errors

def calculate_debt_progress():
    """Calculate debt repayment progress"""
    from app.models import Debt
    debt = Debt.query.first()
    
    if not debt or debt.total_debt == 0:
        return {
            'percentage': 0,
            'remaining': 0,
            'paid': 0,
            'total': 0
        }
    
    paid = debt.total_debt - debt.remaining_debt
    percentage = (paid / debt.total_debt) * 100
    
    return {
        'percentage': float(percentage),
        'remaining': float(debt.remaining_debt),
        'paid': float(paid),
        'total': float(debt.total_debt)
    }

def create_notification(user_id, title, message):
    """Create a new notification for a user"""
    from app.models import Notification
    notification = Notification(
        user_id=user_id,
        title=title,
        message=message,
        is_read=False
    )
    return notification

def get_upcoming_settlements(limit=5):
    """Get upcoming settlement dates"""
    today = date.today()
    upcoming = []
    
    for i in range(limit):
        next_date = today + timedelta(days=i * 7)
        week_start, week_end = week_start_end_for_date(next_date)
        
        # Check if settlement already exists
        from app.models import Settlement
        existing = Settlement.query.filter_by(week_start=week_start).first()
        
        if not existing:
            upcoming.append({
                'week_start': week_start,
                'week_end': week_end,
                'is_last_week': is_last_week_of_month(week_start, week_end)
            })
    
    return upcoming

def export_settlements_to_csv(settlements):
    """Export settlements data to CSV format"""
    import csv
    from io import StringIO
    
    output = StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        'Week Start', 'Week End', 'Total Income', 'Total Expenses', 
        'Salary Deduction', 'Debt Deduction', 'Rent Deduction', 
        'Milk Deduction', 'Total Advances', 'Net Distributable'
    ])
    
    # Write data
    for settlement in settlements:
        writer.writerow([
            settlement.week_start.strftime('%Y-%m-%d'),
            settlement.week_end.strftime('%Y-%m-%d'),
            float(settlement.total_income),
            float(settlement.total_expenses),
            float(settlement.salary_deduction),
            float(settlement.debt_deduction),
            float(settlement.rent_deduction or 0),
            float(settlement.milk_deduction or 0),
            float(settlement.total_advances),
            float(settlement.net_distributable)
        ])
    
    return output.getvalue()

def calculate_weekly_averages():
    """Calculate average weekly financial metrics"""
    from app.models import Settlement
    settlements = Settlement.query.all()
    
    if not settlements:
        return {}
    
    total_weeks = len(settlements)
    avg_income = sum(s.total_income for s in settlements) / total_weeks
    avg_expenses = sum(s.total_expenses for s in settlements) / total_weeks
    avg_net = sum(s.net_distributable for s in settlements) / total_weeks
    avg_advances = sum(s.total_advances for s in settlements) / total_weeks
    
    return {
        'avg_income': quant(avg_income),
        'avg_expenses': quant(avg_expenses),
        'avg_net': quant(avg_net),
        'avg_advances': quant(avg_advances),
        'total_weeks': total_weeks
    }
