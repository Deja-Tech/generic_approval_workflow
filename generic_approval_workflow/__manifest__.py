# -*- coding: utf-8 -*-
{
    'name': 'Generic Configurable Approval Workflow',
    'version': '19.0.1.0',
    'summary': 'Configurable multi-stage approval workflows for any model',
    'description': """
Generic Configurable Approval Workflow
======================================
Define approval workflows that can be applied to any model, without development:

* Workflow configuration per target model, with trigger conditions.
* Dynamic approval stages: add, remove, reorder, and configure at any time.
* Approvers by user, employee, job position or the employee's manager.
* Approval methods: one approver, all approvers, or a minimum number.
* Configurable actions per stage: approve, reject, send back, cancel, reset to draft.
* Configurable behaviour on rejection and on send back.
* Email template per stage and per event.
* Dynamic buttons shown only to the approvers of the current stage.
* Full approval history / audit trail on every record.
    """,
    'category': 'Extra Tools',
    'license': 'OPL-1',
    'author': 'Deja-Tech',
    'price': 79.0,
    'currency': 'EUR',
    'depends': ['base', 'mail', 'hr'],
    'data': [
        'security/approval_security.xml',
        'security/ir.model.access.csv',
        'data/mail_template_data.xml',
        'views/approval_workflow_views.xml',
        'views/approval_history_views.xml',
        'wizard/approval_action_wizard_views.xml',
        'views/approval_menus.xml',
    ],
    'images': ['static/description/banner.png'],
    'installable': True,
    'application': True,
}
