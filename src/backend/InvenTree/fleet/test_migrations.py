"""Tests for the fleet app database migrations."""

from datetime import date

from django_test_migrations.contrib.unittest_case import MigratorTestCase

FROM = ('fleet', '0006_p7_device_state')
TO = ('fleet', '0007_inventree_conventions')


def make_old_data(apps):
    """Create one row of each converted field, in the 0006 schema."""
    User = apps.get_model('auth', 'user')
    Part = apps.get_model('part', 'part')
    StockItem = apps.get_model('stock', 'stockitem')
    FleetDeviceType = apps.get_model('fleet', 'fleetdevicetype')
    Deployment = apps.get_model('fleet', 'deployment')
    DataStream = apps.get_model('fleet', 'datastream')
    Alert = apps.get_model('fleet', 'alert')
    FieldTrip = apps.get_model('fleet', 'fieldtrip')
    DeviceLink = apps.get_model('fleet', 'devicelink')

    user = User.objects.create(username='skipper')

    part = Part.objects.create(
        name='Aurora',
        description='Hydrophone',
        assembly=True,
        trackable=True,
        level=0,
        tree_id=0,
        lft=0,
        rght=0,
    )

    item = StockItem.objects.create(
        part=part, quantity=1, serial='1001', level=0, tree_id=0, lft=0, rght=0
    )

    device_type = FleetDeviceType.objects.create(part=part)

    deployment = Deployment.objects.create(
        reference='DP-0001',
        reference_int=1,
        device_type=device_type,
        device=item,
        health='CRITICAL',
    )

    DataStream.objects.create(deployment=deployment, key='hydrophone', state='LATE')

    Alert.objects.create(
        reference='AL-00001',
        reference_int=1,
        deployment=deployment,
        alert_type='STREAM_LATE',
        severity='INFO',
    )

    FieldTrip.objects.create(
        reference='TRIP-0001',
        reference_int=1,
        title='Algarve',
        start_date=date(2026, 10, 1),
        responsible=user,
    )

    DeviceLink.objects.create(stock_item=item, platform_id='AUR-1', notes='Spare')

    return user


class TestConventionsForward(MigratorTestCase):
    """Migration 0007 converts the text codes, the responsible user and the notes."""

    migrate_from = FROM
    migrate_to = TO

    def prepare(self):
        """Create data in the 0006 schema."""
        self.user_pk = make_old_data(self.old_state.apps).pk

    def test_converted(self):
        """Every value is carried over."""
        apps = self.new_state.apps

        deployment = apps.get_model('fleet', 'deployment').objects.get()
        self.assertEqual(deployment.health, 40)

        stream = apps.get_model('fleet', 'datastream').objects.get()
        self.assertEqual(stream.state, 30)

        alert = apps.get_model('fleet', 'alert').objects.get()
        self.assertEqual(alert.severity, 10)

        trip = apps.get_model('fleet', 'fieldtrip').objects.get()
        self.assertEqual(trip.responsible.owner_id, self.user_pk)
        self.assertEqual(trip.responsible.owner_type.model, 'user')

        link = apps.get_model('fleet', 'devicelink').objects.get()
        self.assertEqual(link.comment, 'Spare')


class TestConventionsReverse(MigratorTestCase):
    """Migration 0007 can be reversed without losing the converted values."""

    migrate_from = TO
    migrate_to = FROM

    def prepare(self):
        """Create data in the 0007 schema."""
        apps = self.old_state.apps
        User = apps.get_model('auth', 'user')
        ContentType = apps.get_model('contenttypes', 'contenttype')
        Owner = apps.get_model('users', 'owner')
        Part = apps.get_model('part', 'part')
        StockItem = apps.get_model('stock', 'stockitem')
        FleetDeviceType = apps.get_model('fleet', 'fleetdevicetype')
        Deployment = apps.get_model('fleet', 'deployment')
        DataStream = apps.get_model('fleet', 'datastream')
        Alert = apps.get_model('fleet', 'alert')
        FieldTrip = apps.get_model('fleet', 'fieldtrip')

        user = User.objects.create(username='skipper')
        self.user_pk = user.pk

        user_type, _created = ContentType.objects.get_or_create(
            app_label='auth', model='user'
        )
        owner, _created = Owner.objects.get_or_create(
            owner_type=user_type, owner_id=user.pk
        )

        part = Part.objects.create(
            name='Aurora',
            description='Hydrophone',
            assembly=True,
            trackable=True,
            level=0,
            tree_id=0,
            lft=0,
            rght=0,
        )
        item = StockItem.objects.create(
            part=part, quantity=1, serial='1001', level=0, tree_id=0, lft=0, rght=0
        )
        deployment = Deployment.objects.create(
            reference='DP-0001',
            reference_int=1,
            device_type=FleetDeviceType.objects.create(part=part),
            device=item,
            health=30,
        )
        DataStream.objects.create(deployment=deployment, key='hydrophone', state=40)
        Alert.objects.create(
            reference='AL-00001',
            reference_int=1,
            deployment=deployment,
            alert_type='STREAM_LATE',
            severity=30,
        )
        FieldTrip.objects.create(
            reference='TRIP-0001',
            reference_int=1,
            title='Algarve',
            start_date=date(2026, 10, 1),
            responsible=owner,
        )

    def test_reverted(self):
        """The text codes and the responsible user come back."""
        apps = self.new_state.apps

        self.assertEqual(
            apps.get_model('fleet', 'deployment').objects.get().health, 'DEGRADED'
        )
        self.assertEqual(
            apps.get_model('fleet', 'datastream').objects.get().state, 'MISSING'
        )
        self.assertEqual(
            apps.get_model('fleet', 'alert').objects.get().severity, 'CRITICAL'
        )
        self.assertEqual(
            apps.get_model('fleet', 'fieldtrip').objects.get().responsible_id,
            self.user_pk,
        )
