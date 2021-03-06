"""Change dependency fk

Revision ID: 3532a68d11c9
Revises: 4926285d0b76
Create Date: 2016-02-01 09:06:22.448764

"""

# revision identifiers, used by Alembic.
revision = '3532a68d11c9'
down_revision = '4926285d0b76'

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.drop_constraint(u'dependency_package_id_fkey', 'dependency', type_='foreignkey')
    op.drop_index('ix_dependency_composite', table_name='dependency')
    op.add_column('dependency', sa.Column('build_id', sa.Integer()))
    op.execute("""CREATE INDEX asdf ON build(package_id, repo_id, id DESC)""")
    op.execute("""UPDATE dependency SET build_id = (
               SELECT id FROM build WHERE build.repo_id=dependency.repo_id
               AND build.package_id=dependency.package_id ORDER BY id DESC LIMIT 1)""")
    op.execute("""DROP INDEX asdf""")
    op.execute("""DELETE FROM dependency WHERE build_id IS NULL""")
    op.alter_column('dependency', 'build_id', nullable=False)
    op.create_index(op.f('ix_dependency_build_id'), 'dependency', ['build_id'],
                    unique=False)
    op.create_foreign_key(None, 'dependency', 'build', ['build_id'], ['id'],
                          ondelete='CASCADE')
    op.drop_column('dependency', 'package_id')
    op.drop_column('dependency', 'repo_id')


def downgrade():
    ### commands auto generated by Alembic - please adjust! ###
    op.add_column('dependency', sa.Column('repo_id', sa.INTEGER(), autoincrement=False, nullable=False))
    op.add_column('dependency', sa.Column('package_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.drop_constraint(None, 'dependency', type_='foreignkey')
    op.create_foreign_key(u'dependency_package_id_fkey', 'dependency', 'package', ['package_id'], ['id'], ondelete=u'CASCADE')
    op.create_index('ix_dependency_composite', 'dependency', ['package_id', 'repo_id'], unique=False)
    op.drop_index(op.f('ix_dependency_build_id'), table_name='dependency')
    op.drop_column('dependency', 'build_id')
    ### end Alembic commands ###
