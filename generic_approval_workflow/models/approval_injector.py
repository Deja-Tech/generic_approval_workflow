# -*- coding: utf-8 -*-
"""Make any configured model approvable without writing a module for it.

Two mechanisms, both driven by the workflow configuration:

* the methods listed on the workflow are wrapped so they first go through the
  approval, and only run once the record is approved;
* an inherited form view is generated for the target model, adding the approval
  buttons to its header.
"""
import logging

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools.sql import column_exists, table_exists

_logger = logging.getLogger(__name__)

APPROVAL_METHODS = {
    'action_approval_submit': 'action_submit',
    'action_approval_approve': 'action_approve',
    'action_approval_reject': 'action_reject',
    'action_approval_send_back': 'action_send_back',
    'action_approval_cancel': 'action_cancel',
    'action_approval_reset_draft': 'action_reset_draft',
}


def _make_approval_action(engine_method):
    """Button method added on the target model, delegating to approval.workflow.record."""
    def action(self, comment=False, **kwargs):
        Approval = self.env['approval.workflow.record']
        for record in self:
            approval = Approval._get_or_create(record)
            if not approval:
                raise UserError(_(
                    'No approval workflow matches this record.'))
            stage_id = self.env.context.get('approval_stage_id')
            if engine_method == 'action_approve' and approval.state == 'draft':
                # one-click approval: start the workflow, then approve the stage
                approval.with_context(
                    approval_stage_id=stage_id).action_submit()
            elif stage_id and approval.stage_id.id != stage_id:
                # the button carries its own stage: make it the current one
                approval.sudo().write({'stage_id': stage_id, 'state': 'pending'})
            method = getattr(approval, engine_method)
            if engine_method in ('action_approve', 'action_reject',
                                 'action_send_back', 'action_cancel'):
                method(comment=comment)
            else:
                method()
        return True
    return action


def _make_approval_helpers():
    def approval_state(self):
        approval = self.env['approval.workflow.record'].sudo().search(
            [('res_model', '=', self._name), ('res_id', '=', self.id)], limit=1)
        return approval

    def open_wizard(self, action_type):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Approval Comment'),
            'res_model': 'approval.action.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self._name,
                'default_res_id': self.id,
                'default_action_type': action_type,
            },
        }

    def action_approval_reject_wizard(self):
        return self._open_approval_wizard('reject')

    def action_approval_send_back_wizard(self):
        return self._open_approval_wizard('send_back')

    def action_view_approval_history(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Approval History'),
            'res_model': 'approval.history',
            'view_mode': 'list,form',
            'domain': [('res_model', '=', self._name), ('res_id', '=', self.id)],
        }

    return {
        '_get_approval': approval_state,
        '_open_approval_wizard': open_wizard,
        'action_approval_reject_wizard': action_approval_reject_wizard,
        'action_approval_send_back_wizard': action_approval_send_back_wizard,
        'action_view_approval_history': action_view_approval_history,
    }


def _make_blocked_method(method_name, original):
    """Refuse a business method while the record waits for an approval."""
    def wrapper(self, *args, **kwargs):
        if not self.env.context.get('skip_approval_workflow'):
            Approval = self.env['approval.workflow.record']
            for record in self:
                approval = Approval.sudo().search(
                    [('res_model', '=', record._name), ('res_id', '=', record.id)], limit=1)
                if approval.state == 'pending':
                    raise UserError(_(
                        'The record %(name)s is waiting for approval at stage "%(stage)s".',
                        name=record.display_name, stage=approval.stage_id.name or ''))
        return original(self, *args, **kwargs)
    wrapper._approval_wrapped = True
    wrapper.__name__ = method_name
    return wrapper


def _make_state_tracker(original_write, state_fields):
    """After a state change, put the record on the matching approval stage."""
    def wrapper(self, vals):
        res = original_write(self, vals)
        if not self.env.context.get('skip_approval_sync') and (set(vals) & state_fields):
            Approval = self.env['approval.workflow.record'].sudo()
            for record in self:
                Approval._sync_for_record(record)
        return res
    wrapper._approval_wrapped = True
    wrapper.__name__ = 'write'
    return wrapper


def _make_state_tracker_create(original_create, state_fields):
    def wrapper(self, vals_list):
        records = original_create(self, vals_list)
        if not self.env.context.get('skip_approval_sync'):
            Approval = self.env['approval.workflow.record'].sudo()
            for record in records:
                Approval._sync_for_record(record)
        return records
    wrapper._approval_wrapped = True
    wrapper.__name__ = 'create'
    return wrapper


class ApprovalWorkflowRegistry(models.AbstractModel):
    """Applies the configuration to the registry at load time."""
    _name = 'approval.registry'
    _description = 'Approval Workflow Registry Hook'

    def _register_hook(self):
        res = super()._register_hook()
        if not self._approval_schema_ready():
            # module not installed or not upgraded yet: nothing to apply
            return res
        try:
            # a savepoint keeps a bad configuration from aborting the whole
            # transaction, which would break the rest of the registry load
            with self.env.cr.savepoint():
                self._apply_approval_workflows()
        except Exception:            # never break the registry on a bad config
            _logger.exception('Could not apply the approval workflows')
        return res

    def _approval_schema_ready(self):
        """Whether the configuration table has every column the hook reads."""
        cr = self.env.cr
        if not table_exists(cr, 'approval_workflow'):
            return False
        required = ('model_id', 'active', 'blocked_methods', 'inject_buttons')
        missing = [c for c in required if not column_exists(cr, 'approval_workflow', c)]
        if missing:
            _logger.info(
                'Approval workflows not applied yet, upgrade the module '
                '(missing columns: %s)', ', '.join(missing))
            return False
        return True

    @api.model
    def _apply_approval_workflows(self):
        workflows = self.env['approval.workflow'].sudo().search([('active', '=', True)])
        helpers = _make_approval_helpers()
        patched = workflows.browse()
        # 1. the methods must exist before any view referencing them is validated
        for workflow in workflows:
            Model = self.env.registry.get(workflow.model_name)
            if not Model:
                continue
            # generic approval buttons
            for method_name, engine_method in APPROVAL_METHODS.items():
                setattr(Model, method_name, _make_approval_action(engine_method))
            for name, helper in helpers.items():
                setattr(Model, name, helper)
            # follow the state of the record, to switch approval stage
            state_fields = set()
            for state in workflow.stage_ids.filtered(
                    lambda l: l.line_type == 'state').mapped('model_state_id'):
                if (state.field_name or 'state') in Model._fields:
                    state_fields.add(state.field_name or 'state')
            if state_fields:
                original_write = getattr(Model, 'write', None)
                if original_write and not getattr(original_write, '_approval_wrapped', False):
                    setattr(Model, 'write', _make_state_tracker(original_write, state_fields))
                original_create = getattr(Model, 'create', None)
                if original_create and not getattr(original_create, '_approval_wrapped', False):
                    setattr(Model, 'create',
                            _make_state_tracker_create(original_create, state_fields))

            # block the configured business methods
            for method_name in workflow._get_blocked_methods():
                original = getattr(Model, method_name, None)
                if not original or getattr(original, '_approval_wrapped', False):
                    continue
                setattr(Model, method_name, _make_blocked_method(method_name, original))
            patched |= workflow

        # 2. now the buttons resolve, so the generated views validate
        # always re-sync: the generated arch changes when this module is updated
        # لا يتم إنشاء أو تعديل الحقول هنا: أي تعديل على ir.model.fields أثناء
        # تحميل السجل يعيد تشغيل هذا الخطاف ويسبب تكرارا لا نهائيا.
        # إنشاء الحقول يتم من زر "تطبيق الإعدادات" أو عند حفظ المسار.
        to_sync = patched.filtered(lambda w: w.inject_buttons)
        if to_sync:
            to_sync._sync_form_view()
