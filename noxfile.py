import nox


@nox.session()
def python3_django111(session):
    session.install('django-crispy-forms==1.9.2', 'docutils==0.16', 'django-admin-honeypot==1.1.0')
    session.install('-e', '.')
    session.run('python', '-B', '-R', '-tt', '-W', 'ignore', '-m', 'shouty')

@nox.session()
def python3_django19(session):
    session.install('django-crispy-forms==1.8.1', 'docutils==0.16', 'django-admin-honeypot==1.1.0')
    session.install('-e', '.')
    session.run('python', '-B', '-R', '-tt', '-W', 'ignore', '-m', 'shouty')

@nox.session()
def python3_django22(session):
    session.install('django-crispy-forms==1.9.2', 'docutils==0.16', 'django-admin-honeypot==1.1.0')
    session.install('-e', '.')
    session.run('python', '-B', '-R', '-tt', '-W', 'ignore', '-m', 'shouty')

@nox.session()
def python3_django31(session):
    session.install('django-crispy-forms==1.9.2', 'docutils==0.16', 'django-admin-honeypot==1.1.0')
    session.install('-e', '.')
    session.run('python', '-B', '-R', '-tt', '-W', 'ignore', '-m', 'shouty')
