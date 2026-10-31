"""police and Gendarmerie stations, units and requests

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('police_stations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('force', sa.String(length=12), nullable=False),
    sa.Column('lat', sa.Double(), nullable=False),
    sa.Column('lon', sa.Double(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('police_units',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('station_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('force', sa.String(length=12), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('lat', sa.Double(), nullable=True),
    sa.Column('lon', sa.Double(), nullable=True),
    sa.Column('located_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['station_id'], ['police_stations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('police_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('incident_id', sa.Integer(), nullable=False),
    sa.Column('unit_id', sa.String(length=40), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('distance_m', sa.Double(), nullable=False),
    sa.Column('eta_s', sa.Integer(), nullable=False),
    sa.Column('route', sa.JSON(), nullable=True),
    sa.Column('requested_at', sa.DateTime(), nullable=False),
    sa.Column('arrived_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['incident_id'], ['incidents.id'], ),
    sa.ForeignKeyConstraint(['unit_id'], ['police_units.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_police_requests_incident_id'), 'police_requests', ['incident_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_police_requests_incident_id'), table_name='police_requests')
    op.drop_table('police_requests')
    op.drop_table('police_units')
    op.drop_table('police_stations')
