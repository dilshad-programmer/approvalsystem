from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from approval_system.models import UserProfile


class Command(BaseCommand):
    help = 'Seed initial users for the application'

    USERS = [
        {'username': 'shawn',  'password': 'Shawn@1234',  'email': 'shawn@example.com',  'role': 'REQUESTER'},
        {'username': 'ajay',   'password': 'Ajay@1234',   'email': 'ajay@example.com',   'role': 'APPROVER'},
        {'username': 'pravin', 'password': 'Pravin@1234', 'email': 'pravin@example.com', 'role': 'ADMIN'},
    ]

    def handle(self, *args, **kwargs):
        for u in self.USERS:
            user, created = User.objects.get_or_create(username=u['username'])
            user.set_password(u['password'])
            user.email = u['email']
            user.save()

            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = u['role']
            profile.save()

            status = 'Created' if created else 'Updated'
            self.stdout.write(self.style.SUCCESS(f'{status}: {u["username"]} ({u["role"]})'))

        self.stdout.write(self.style.SUCCESS('Seed complete.'))
