#!/usr/bin/python3 -tt
# -*- coding: utf-8 -*-

__all__ = ['make_site']

from sqlalchemy import *
from sqlalchemy.exc import *
from werkzeug.exceptions import *
from pichator.site.util import *
from functools import wraps


from sqlalchemy import desc
from sqlalchemy.exc import SQLAlchemyError
from datetime import date, datetime, timedelta
from dateutil.relativedelta import *
from xml.sax.saxutils import escape


from twisted.python import log


import flask
import os
import re


def make_site(manager, access_model, debug=False):
    app = flask.Flask('.'.join(__name__.split('.')[:-1]))
    app.secret_key = os.urandom(16)
    app.debug = debug

    @app.template_filter('to_alert')
    def category_to_alert(category):
        return {
            'warning': 'alert-warning',
            'error': 'alert-danger',
        }[category]

    @app.template_filter('to_icon')
    def category_to_icon(category):
        return {
            'warning': 'pficon-warning-triangle-o',
            'error': 'pficon-error-circle-o',
        }[category]

    def has_privilege(privilege):
        roles = flask.request.headers.get('X-Roles', '')

        if not roles or '(null)' == roles:
            roles = ['impotent']
        else:
            roles = re.findall(r'\w+', roles)

        return access_model.have_privilege(privilege, roles)

    def pass_user_info(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            uid = flask.request.headers.get('X-User-Id', '0')
            username = flask.request.headers.get('X-Full-Name', 'brabemi')

            kwargs.update({
                'uid': int(uid),
                'username': username.encode('latin1').decode('utf8'),
            })

            return fn(*args, **kwargs)
        return wrapper

    def authorized_only(privilege='user'):
        def make_wrapper(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                if not has_privilege(privilege):
                    raise Forbidden('RBAC Forbidden')

                return fn(*args, **kwargs)

            return wrapper
        return make_wrapper

    @app.errorhandler(Forbidden.code)
    def unauthorized(e):
        return flask.render_template('forbidden.html')

    @app.errorhandler(ImATeapot.code)
    def imateapot(e):
        return flask.render_template('teapot.html')

    @app.errorhandler(NotAcceptable.code)
    def notacceptable(e):
        return flask.render_template('not_acceptable.html')

    @app.errorhandler(InternalServerError.code)
    def internalservererror(e):
        return flask.render_template('internal_server_error.html')

    @app.route('/')
    @authorized_only('admin')
    @pass_user_info
    def index(uid, username):
        nonlocal has_privilege
        emp_no = manager.get_emp_no(username)
        acl = manager.get_acl(username)
        log.msg(f'acl is {acl}')
        if acl == 'readonly':
            return flask.render_template('attendance_ro.html', **locals())
        elif acl.isdigit():
            dept = int(acl)
            return flask.render_template('attendance_manager.html', **locals())
        else:
            return flask.render_template('attendance.html', **locals())

    @app.route('/get_emp')
    @authorized_only('admin')
    @pass_user_info
    def employees(uid, username):
        dept = flask.request.values.get('dept')
        period = flask.request.values.get('period')
        return flask.jsonify(manager.get_employees(dept, period))
    
    @app.route('/dept')
    @authorized_only('admin')
    @pass_user_info
    def display_dept(uid, username):
        acl = manager.get_acl(username)
        return flask.render_template('attendance_department.html', **locals())

    @app.route('/dept_data')
    @authorized_only('admin')
    @pass_user_info
    def get_dept(uid, username):
        dept = flask.request.values.get('dept')
        if not dept:
            log.err(
                'Geting data for department without mandatory parameter department number.')
            raise NotAcceptable
        acl = manager.get_acl(username)
        if acl != str(dept)[0] and acl != 'admin':
            log.err(
                f'Trying to acces data of department {dept}, but has no authorization to do so.')
            raise Forbidden
        period = flask.request.values.get('period')
        if not period:
            today = datetime.today()
            period = f'{today.month}-{today.year}'
        return flask.jsonify(manager.get_department(dept, period))

    @app.route('/timetable', methods=['GET', 'POST'])
    @authorized_only('user')
    @pass_user_info
    def show_timetable(uid, username):
        acl = manager.get_acl(username)
        nonlocal has_privilege
        emp_no = manager.get_emp_no(username)
        if flask.request.method == 'GET':
            return flask.render_template('timetable.html', **locals())
        else:
            data_dict = flask.request.form.to_dict()
            if not manager.set_timetables(data_dict):
                flask.flash(
                    'Počet hodin v rozvrhu neodpovídá úvazku.', 'error')
            return flask.render_template('timetable.html', **locals())

    @app.route('/timetable_data')
    @authorized_only('admin')
    @pass_user_info
    def get_timetables_data(uid, username):
        acl = manager.get_acl(username)
        nonlocal has_privilege
        emp_no = manager.get_emp_no(username)
        if not emp_no:
            log.err('Query for timetable data for employee who is not in database.')
            raise NotAcceptable
        return flask.jsonify(manager.get_timetables(uid))

    @app.route('/pvs')
    @authorized_only('admin')
    @pass_user_info
    def get_pvs(uid, username):
        nonlocal has_privilege
        period = flask.request.values.get('period')

        if not period:
            log.err('Query for list of PVs without required period parameter.')
            raise NotAcceptable

        emp_no = manager.get_emp_no(username)

        return flask.jsonify(manager.get_pvs(emp_no, period))

    @app.route('/attendance_data')
    @authorized_only('admin')
    @pass_user_info
    def get_attendance_data(uid, username):
        nonlocal has_privilege

        pvid = flask.request.values.get('pvid')
        if flask.request.values.get('username'):
            username = flask.request.values.get('username')
        emp_no = manager.get_emp_no(username)
        if not pvid or not emp_no:
            log.err(
                'Query for attendance data without required parameter pvid or emp_no.')
            raise NotAcceptable
        if str(emp_no) != pvid.split('.')[0] and not manager.get_acl(username).isdigit():
            log.err('Requesting attendance data for user other than is logged-in.')
            raise Forbidden
        period = flask.request.values.get('period')

        if not pvid or not period:
            log.err(
                'Query for attendance data without required parameter pvid or period.')
            raise NotAcceptable

        return flask.jsonify(manager.get_attendance(uid, pvid, period, username))

    @app.route('/attendance_submit')
    @authorized_only('admin')
    @pass_user_info
    def set_attendance_data(uid, username):
        nonlocal has_privilege
        pvid = flask.request.values.get('pvid')
        acl = manager.get_acl(username)
        if acl.isdigit():
            username = manager.pvid_to_username(pvid)
            if acl not in manager.get_depts(username) and acl not in [str(i)[0] for i in manager.get_depts(username)]:
                log.err('Submiting data for person not in your department.')
                raise Forbidden
        emp_no = manager.get_emp_no(username)
        if not pvid or not emp_no:
            log.err(
                'Query for attendance data without required parameter pvid or emp_no.')
            raise NotAcceptable
        if str(emp_no) != pvid.split('.')[0]:
            log.err('Submiting attendance data for user other than is logged-in.')
            raise Forbidden
        period = flask.request.values.get('period')

        if not period:
            log.err(
                'Query for attendance data without required parameter period.')
            raise NotAcceptable
        start = flask.request.values.get('start')
        end = flask.request.values.get('end')
        if not start or not end:
            log.err(
                'Query for attendance data without required parameter start or end.')
            raise NotAcceptable
        mode = flask.request.values.get('mode')
        if not mode:
            mode = 'Absence'
        day = flask.request.values.get('day')
        if not day:
            log.err('Query without required parameter day.')
            raise NotAcceptable

        return flask.jsonify(manager.set_attendance(day, period, username, start, end, mode))

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        manager.pich_db.rollback()

    return app


# vim:set sw=4 ts=4 et:
# -*- coding: utf-8 -*-
