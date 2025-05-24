"""seed transit modes

Revision ID: seed_transit_modes
Revises: 531666e625e2
Create Date: 2024-03-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'seed_transit_modes'
down_revision = '531666e625e2'
branch_labels = None
depends_on = None

def upgrade():
    # Insert default transit modes
    op.execute("""
        INSERT INTO transit_mode_options (mode) VALUES 
        ('car'),
        ('bike'),
        ('bus_train'),
        ('walking'),
        ('rideshare')
    """)

def downgrade():
    # Remove all transit modes
    op.execute("DELETE FROM transit_mode_options") 