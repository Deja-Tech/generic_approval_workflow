# -*- coding: utf-8 -*-

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ApprovalRecord(models.Model):
    """Approval state of any record, without touching the target model."""
    _name = 'approval.workflow.record'
    _description = 'Approval Record'
    _order = 'id desc'
    _rec_name = 'res_name'

    workflow_id = fields.Many2one('approval.workflow', string='Workflow', required=True,
                                  ondelete='cascade')
    stage_id = fields.Many2one('approval.workflow.stage', string='Current Stage',
                               ondelete='set null')
    res_model = fields.Char(string='Model', required=True, index=True)
    res_id = fields.Integer(string='Record ID', required=True, index=True)
    res_name = fields.Char(string='Record', compute='_compute_res_name', store=True)
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('pending', 'Pending Approval'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('cancel', 'Cancelled'),
        ],
        string='Approval State', default='draft', required=True)
    status_label = fields.Char(string='Status', compute='_compute_status_label')
    can_approve = fields.Boolean(string='Can Approve', compute='_compute_can_approve')
    approver_ids = fields.Many2many('res.users', string='Current Approvers',
                                    compute='_compute_can_approve',
                                    search='_search_approver_ids')
    progress = fields.Char(string='Approvals', compute='_compute_can_approve')
    cycle = fields.Integer(
        string='Approval Round', default=1, readonly=True,
        help='A new round starts every time the record changes state, so the '
             'stages of that state can be approved again.')
    last_state = fields.Char(
        string='Last State', readonly=True,
        help='State of the record when the current round started.')
    company_id = fields.Many2one('res.company', string='Company',
                                 default=lambda self: self.env.company)

    _sql_constraints = [
        ('res_uniq', 'unique(res_model, res_id)', 'A record can only have one approval.'), ]

    @api.depends('res_model', 'res_id')
    def _compute_res_name(self):
        for approval in self:
            record = approval._get_record()
            approval.res_name = record.display_name if record else ''

    @api.depends('state', 'stage_id')
    def _compute_status_label(self):
        for approval in self:
            if approval.state == 'pending' and approval.stage_id:
                approval.status_label = _('Pending %s', approval.stage_id.name)
            else:
                approval.status_label = dict(
                    self._fields['state'].selection).get(approval.state, '')

    def _search_approver_ids(self, operator, value):
        """Approvals whose current stage has the given user as approver."""
        if operator not in ('in', 'not in', '=', '!='):
            raise UserError(_('Unsupported operator on Current Approvers.'))
        user_ids = value if isinstance(value, (list, tuple)) else [value]
        users = self.env['res.users'].browse([u for u in user_ids if isinstance(u, int)])
        pending = self.sudo().search([('state', '=', 'pending')])
        matching = pending.filtered(
            lambda a: bool(set(users.ids) & set(a._get_approver_users().ids)))
        negative = operator in ('not in', '!=')
        return [('id', 'not in' if negative else 'in', matching.ids)]

    @api.depends('state', 'stage_id')
    def _compute_can_approve(self):
        for approval in self:
            approvers = approval._get_approver_users()
            approval.approver_ids = approvers
            approval.can_approve = approval._can_approve()
            record = approval._get_record()
            if approval.stage_id and record:
                approval.progress = '%s / %s' % (
                    len(approval._get_stage_approvals()),
                    approval.stage_id._get_required_approvals(record))
            else:
                approval.progress = ''

    @api.model_create_multi
    def create(self, vals_list):
        approvals = super().create(vals_list)
        approvals._sync_markers(False)
        return approvals

    def write(self, vals):
        res = super().write(vals)
        if 'state' in vals or 'stage_id' in vals:
            self._sync_markers(False)
        return res

    # ------------------------------------------------------------------
    def _get_record(self):
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            return False
        record = self.env[self.res_model].browse(self.res_id).exists()
        return record or False

    @api.model
    def _get_or_create(self, record):
        """Approval of a record, created against the matching workflow."""
        approval = self.sudo().search(
            [('res_model', '=', record._name), ('res_id', '=', record.id)], limit=1)
        if approval:
            return approval
        workflow = self.env['approval.workflow']._get_workflow_for_record(record)
        if not workflow:
            return self.browse()
        return self.sudo().create({
            'workflow_id': workflow.id,
            'res_model': record._name,
            'res_id': record.id,
        })

    @api.model
    def _sync_for_record(self, record):
        """تحديد الخطوة التالية للسجل حسب ترتيب أسطر المسار.

        القاعدة واحدة لكل النماذج: ننظر إلى موضع السجل في المسار، فإن كان
        السطر التالي مرحلة موافقة صارت هي الخطوة الواجبة، وإلا فالسجل حر
        ليتحرك بأزرار النموذج نفسه.
        """
        workflow = self.env['approval.workflow']._get_workflow_for_record(record)
        if not workflow:
            # السجل خارج معيار تشغيل المسار: نرفع أي أثر سابق ليتحرك بأزراره
            approval = self.sudo().search(
                [('res_model', '=', record._name), ('res_id', '=', record.id)], limit=1)
            if approval:
                if approval.state == 'pending':
                    approval.sudo().write({'stage_id': False, 'state': 'draft'})
                approval._sync_markers(record, False)
            elif ('x_approval_next_stage' in record._fields
                  and record.x_approval_next_stage):
                record.sudo().with_context(
                    skip_approval_workflow=True, skip_approval_sync=True
                ).write({'x_approval_next_stage': ''})
            return self.browse()
        approval = self._get_or_create(record)
        if not approval:
            return self.browse()

        state_field = workflow._get_state_field_name()
        current_state = record[state_field] if state_field in record._fields else False
        if current_state and approval.last_state != current_state:
            # السجل انتقل إلى حالة أخرى: تبدأ جولة جديدة والشريط يتبع الحالة
            approval.sudo().write({
                'cycle': (approval.cycle or 1) + 1,
                'last_state': current_state,
            })
            approval._write_workflow_state(record, current_state)

        stage = workflow._get_next_stage_for_record(record)
        if stage and not stage._match_record(record):
            stage = self.env['approval.workflow.stage']

        if stage:
            if approval.stage_id != stage or approval.state != 'pending':
                approval.sudo().write({'stage_id': stage.id, 'state': 'pending'})
                approval._log('submit', approval.status_label, stage=stage)
                approval._notify(stage)
        elif approval.state == 'pending':
            approval.sudo().write({'stage_id': False, 'state': 'draft'})

        approval._sync_markers(record, stage)
        return approval

    def _sync_markers(self, record, stage=None):
        """كتابة المؤشرات التقنية على السجل: الخطوة التالية وشريط الحالة."""
        for approval in self:
            target = record or approval._get_record()
            if not target:
                continue
            values = {}
            next_value = str(stage.id) if stage else ''
            if ('x_approval_next_stage' in target._fields
                    and (target.x_approval_next_stage or '') != next_value):
                values['x_approval_next_stage'] = next_value
            pending = (str(approval.stage_id.id)
                       if (approval.stage_id and approval.state == 'pending') else '')
            if ('x_approval_stage' in target._fields
                    and (target.x_approval_stage or '') != pending):
                values['x_approval_stage'] = pending
            if ('x_approval_state' in target._fields
                    and target.x_approval_state != approval.state):
                values['x_approval_state'] = approval.state
            state_field = approval.workflow_id._get_state_field_name()
            if ('x_workflow_state' in target._fields and not target.x_workflow_state
                    and state_field in target._fields and target[state_field]):
                values['x_workflow_state'] = target[state_field]
            if values:
                target.sudo().with_context(
                    skip_approval_workflow=True, skip_approval_sync=True).write(values)

    @api.model
    def _is_stage_done(self, record, stage):
        """Whether that stage was already approved in the current round."""
        approval = self.sudo().search(
            [('res_model', '=', record._name), ('res_id', '=', record.id)], limit=1)
        return bool(self.env['approval.history'].sudo().search_count([
            ('res_model', '=', record._name),
            ('res_id', '=', record.id),
            ('stage_id', '=', stage.id),
            ('action', '=', 'approve'),
            ('cycle', '=', approval.cycle or 1),
        ]))

    # ------------------------------------------------------------------
    # Approvers
    # ------------------------------------------------------------------
    def _get_approver_users(self):
        self.ensure_one()
        record = self._get_record()
        if not self.stage_id or not record:
            return self.env['res.users']
        return self.stage_id._get_approver_users(record)

    def _get_stage_approvals(self):
        self.ensure_one()
        if not self.stage_id:
            return self.env['approval.history']
        return self.env['approval.history'].sudo().search([
            ('res_model', '=', self.res_model),
            ('res_id', '=', self.res_id),
            ('stage_id', '=', self.stage_id.id),
            ('action', '=', 'approve'),
            ('cycle', '=', self.cycle or 1),
        ])

    def _can_approve(self, user=None):
        self.ensure_one()
        user = user or self.env.user
        if self.state != 'pending' or not self.stage_id:
            return False
        done = self._get_stage_approvals().mapped('user_id')
        return user in self._get_approver_users() and user not in done

    def _check_rights(self):
        self.ensure_one()
        if self._can_approve():
            return
        stage_name = self.stage_id.name or ''
        if self.state != 'pending' or not self.stage_id:
            raise UserError(_(
                'This record is not waiting for an approval.'))
        approvers = self._get_approver_users()
        if not approvers:
            raise UserError(_(
                'No approver is configured on the stage "%s". '
                'Set the approvers in the workflow configuration.', stage_name))
        if self.env.user not in approvers:
            raise UserError(_(
                'You are not an approver of the stage "%(stage)s". '
                'Approvers: %(names)s',
                stage=stage_name,
                names=', '.join(approvers.mapped('name')) or '-'))
        raise UserError(_(
            'You already approved the stage "%s" in this round.', stage_name))

    # ------------------------------------------------------------------
    def _log(self, action, previous=None, comment=False, stage=None):
        self.ensure_one()
        stage = stage if stage is not None else self.stage_id
        return self.env['approval.history'].sudo().create({
            'workflow_id': self.workflow_id.id,
            'stage_id': stage.id if stage else False,
            'stage_name': stage.name if stage else False,
            'res_model': self.res_model,
            'res_model_id': self.env['ir.model']._get_id(self.res_model),
            'res_id': self.res_id,
            'res_name': self.res_name,
            'action': action,
            'user_id': self.env.user.id,
            'previous_status': previous,
            'new_status': self.status_label,
            'comment': comment,
            'cycle': self.cycle or 1,
        })

    def _notify(self, stage, template_field='mail_template_id'):
        self.ensure_one()
        record = self._get_record()
        if not stage or not record:
            return
        template = stage[template_field]
        partners = stage._get_notification_partners(record)
        if template:
            template.sudo().send_mail(
                record.id, force_send=not self.env.context.get('approval_bulk_sync'),
                email_values={'recipient_ids': [(6, 0, partners.ids)]} if partners else None,
                email_layout_xmlid='mail.mail_notification_light')
        elif partners and hasattr(record, 'message_post'):
            record.message_post(
                body=_('Approval required for stage %s.', stage.name),
                partner_ids=partners.ids, subtype_xmlid='mail.mt_comment')

    def _first_stage(self):
        self.ensure_one()
        record = self._get_record()
        stages = self.workflow_id.stage_ids.filtered(
            lambda s: s.line_type == 'approval').sorted(lambda s: (s.sequence, s.id))
        return next((s for s in stages if s._match_record(record)),
                    self.env['approval.workflow.stage'])

    def _next_stage(self):
        self.ensure_one()
        record = self._get_record()
        stage = self.stage_id
        if stage.next_stage_id:
            candidates = stage.next_stage_id
        else:
            candidates = self.workflow_id.stage_ids.filtered(
                lambda s: s.line_type == 'approval'
                          and (s.sequence, s.id) > (stage.sequence, stage.id))
        for candidate in candidates.sorted(lambda s: (s.sequence, s.id)):
            if candidate._match_record(record):
                return candidate
        return self.env['approval.workflow.stage']

    def _previous_stage(self):
        self.ensure_one()
        previous = self.workflow_id.stage_ids.filtered(
            lambda s: s.line_type == 'approval'
                      and (s.sequence, s.id) < (self.stage_id.sequence, self.stage_id.id))
        return previous.sorted(lambda s: (s.sequence, s.id))[-1:]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_submit(self):
        for approval in self:
            if approval.state == 'pending':
                raise UserError(_('This record is already under approval.'))
            previous = approval.status_label
            forced = self.env.context.get('approval_stage_id')
            stage = (self.env['approval.workflow.stage'].browse(forced)
                     if forced else approval._first_stage())
            if not stage:
                approval.sudo().write({'state': 'approved'})
                approval._log('approve', previous)
                continue
            approval.sudo().write({'stage_id': stage.id, 'state': 'pending'})
            approval._log('submit', previous, stage=stage)
            approval._notify(stage)
        return True

    def action_approve(self, comment=False):
        for approval in self:
            approval._check_rights()
            stage = approval.stage_id
            previous = approval.status_label
            approval._log('approve', previous, comment=comment, stage=stage)
            record = approval._get_record()
            if len(approval._get_stage_approvals()) < stage._get_required_approvals(record):
                continue
            approval._log('approve', previous, stage=stage)
            approval._notify(stage, 'approve_template_id')
            record = approval._get_record()
            if record:
                # the new state decides what happens next: another approval, or
                # the record is free again for the buttons of its own model
                approval.sudo().write({'stage_id': False, 'state': 'draft'})
                # السجل يستقر على خطوة المرحلة التي تمت الموافقة عليها
                approval._write_workflow_state(
                    record, stage._get_stage_state_value())
                if stage.target_state_id:
                    # "State after Approval" set on the stage: move the record there
                    approval._apply_target_state(stage)
                self._sync_for_record(record)
        return True

    def _write_state(self, record, value):
        """كتابة حالة النموذج الأصلية دون إعادة تشغيل المزامنة."""
        if not record or not value:
            return
        field_name = self.workflow_id._get_state_field_name() if self.workflow_id else 'state'
        if field_name not in record._fields or record[field_name] == value:
            return
        record.sudo().with_context(
            skip_approval_workflow=True, skip_approval_sync=True).write(
            {field_name: value})

    def _write_workflow_state(self, record, value):
        """تحديث شريط الحالة الجامع (حالات النموذج + مراحل الموافقة)."""
        if not record or not value or 'x_workflow_state' not in record._fields:
            return
        if record.x_workflow_state == value:
            return
        record.sudo().with_context(
            skip_approval_workflow=True, skip_approval_sync=True).write(
            {'x_workflow_state': value})

    def _apply_target_state(self, stage):
        """Move the record to the state configured after this approval stage."""
        self.ensure_one()
        target = stage._get_target_state()
        record = self._get_record()
        if not target or not record:
            return
        field_name = target.field_name or 'state'
        if field_name not in record._fields:
            return
        if record[field_name] != target.value:
            record.sudo().with_context(
                skip_approval_workflow=True).write({field_name: target.value})

    def action_reject(self, comment=False):
        for approval in self:
            approval._check_rights()
            stage = approval.stage_id
            previous = approval.status_label
            behavior = stage.reject_behavior
            if behavior == 'draft':
                approval.sudo().write({'state': 'draft', 'stage_id': False})
            elif behavior == 'previous':
                target = approval._previous_stage()
                approval.sudo().write({'stage_id': target.id or False,
                                       'state': 'pending' if target else 'draft'})
            elif behavior == 'stage' and stage.reject_stage_id:
                approval.sudo().write({'stage_id': stage.reject_stage_id.id,
                                       'state': 'pending'})
            else:
                approval.sudo().write({'state': 'rejected'})
                # نقل السجل إلى حالة الرفض في النموذج نفسه
                reject_state = stage._get_reject_model_state()
                record = approval._get_record()
                if record and reject_state:
                    approval._write_state(record, reject_state.value)
                    approval._write_workflow_state(record, reject_state.value)
            approval._log('reject', previous, comment=comment, stage=stage)
            approval._notify(stage, 'reject_template_id')
        return True

    def action_send_back(self, comment=False):
        for approval in self:
            approval._check_rights()
            stage = approval.stage_id
            previous = approval.status_label
            behavior = stage.return_behavior
            if behavior == 'draft':
                approval.sudo().write({'state': 'draft', 'stage_id': False})
            elif behavior == 'stage' and stage.return_stage_id:
                approval.sudo().write({'stage_id': stage.return_stage_id.id,
                                       'state': 'pending'})
            else:
                target = approval._previous_stage()
                approval.sudo().write({'stage_id': target.id or False,
                                       'state': 'pending' if target else 'draft'})
            approval._log('send_back', previous, comment=comment, stage=stage)
            approval._notify(stage, 'return_template_id')
        return True

    def action_cancel(self, comment=False):
        for approval in self:
            previous = approval.status_label
            approval.sudo().write({'state': 'cancel'})
            approval._log('cancel', previous, comment=comment)
        return True

    def action_open_reject_wizard(self):
        return self._open_wizard('reject')

    def action_open_send_back_wizard(self):
        return self._open_wizard('send_back')

    def _open_wizard(self, action_type):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Approval Comment'),
            'res_model': 'approval.action.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': self.res_model,
                'default_res_id': self.res_id,
                'default_action_type': action_type,
            },
        }

    def action_open_record(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': self.res_model,
            'res_id': self.res_id,
            'view_mode': 'form',
        }

    def action_view_history(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Approval History'),
            'res_model': 'approval.history',
            'view_mode': 'list,form',
            'domain': [('res_model', '=', self.res_model), ('res_id', '=', self.res_id)],
        }

    def action_reset_draft(self):
        for approval in self:
            previous = approval.status_label
            record = approval._get_record()
            first_state = approval.workflow_id.stage_ids.filtered(
                lambda l: l.line_type == 'state').sorted(
                lambda l: (l.sequence, l.id))[:1].model_state_id
            approval.sudo().write({
                'state': 'draft', 'stage_id': False,
                'cycle': (approval.cycle or 1) + 1,
            })
            if record and first_state:
                approval._write_state(record, first_state.value)
                approval._write_workflow_state(record, first_state.value)
            approval._log('reset_draft', previous)
        return True
