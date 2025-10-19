# app/cli.py
import click
from app import create_app, db
from app.models import User
from werkzeug.security import generate_password_hash

@click.command('create-admin')
def create_admin():
    """Create an admin user"""
    app = create_app()
    with app.app_context():
        if User.query.filter_by(username='admin').first():
            click.echo('Admin user already exists!')
            return
        
        admin = User(
            username='admin',
            email='admin@example.com',
            password_hash=generate_password_hash('admin123'),
            role='admin'
        )
        db.session.add(admin)
        db.session.commit()
        click.echo('Admin user created! Username: admin, Password: admin123')

def init_app(app):
    app.cli.add_command(create_admin)
