# -*- coding: utf-8 -*-

import re

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.safe_eval import safe_eval


class ApprovalWorkflow(models.Model):
    _name = 'approval.workflow'
    _description = 'Approval Workflow'
    _order = 'sequence, id'

    name = fields.Char(string='Workflow Name', required=True, translate=True)
    sequence = fields.Integer(
        string='Sequence', default=10,
        help='Workflows are evaluated in this order; the first matching one is applied.')
    model_id = fields.Many2one(
        'ir.model', string='Target Model', required=True, ondelete='cascade',
        domain=[('transient', '=', False)],
        help='Model the approval workflow is applied on.')
    model_name = fields.Char(related='model_id.model', string='Model Name', store=True)
    active = fields.Boolean(string='Active', default=True)
    state_field = fields.Char(
        string='State Field', compute='_compute_state_field', store=True, readonly=False,
        help='Selection field holding the status of the target model. Detected '
             'automatically ("state", or the statusbar field of the form view, e.g. '
             '"contract_type"); change it if the model uses another field.')
    state = fields.Selection(
        selection=[('active', 'Active'), ('inactive', 'Inactive')],
        string='Workflow Status', compute='_compute_state',
        inverse='_inverse_state', store=True)
    condition = fields.Char(
        string='Trigger Criteria', default='[]',
        help="Domain evaluated on the record. The workflow applies when the record "
             "matches it, e.g. [('amount_total', '>', 10000)].")
    stage_ids = fields.One2many(
        'approval.workflow.stage', 'workflow_id', string='Approval Stages', copy=True)
    stage_count = fields.Integer(string='Stages', compute='_compute_stage_count')
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company)
    note = fields.Html(string='Description')
    excluded_state_ids = fields.Many2many(
        'approval.model.state', 'approval_workflow_excluded_state_rel',
        'workflow_id', 'state_id', string='Hidden States',
        help='States removed from the lines. They stay in the model but are hidden '
             'in the backend, with the buttons that only serve them.')
    blocked_methods = fields.Char(
        string='Methods to Approve',
        default='button_confirm,action_confirm',
        help='Comma separated methods of the target model that must go through the '
             'approval, e.g. "button_confirm". Calling one of them submits the record '
             'for approval instead of running it, until the record is approved.')
    inject_buttons = fields.Boolean(
        string='Add Buttons to the Form', default=True,
        help='Generate an inherited form view adding the approval buttons to the '
             'target model, so no development is needed.')
    view_id = fields.Many2one(
        'ir.ui.view', string='Generated View', readonly=True, copy=False,
        ondelete='set null')

    @api.depends('model_id')
    def _compute_state_field(self):
        for workflow in self:
            workflow.state_field = workflow._detect_state_field()

    def _detect_state_field(self):
        """Status field of the target model: 'state', else its statusbar field."""
        self.ensure_one()
        if not self.model_name or self.model_name not in self.env:
            return 'state'
        fields_map = self.env[self.model_name]._fields
        state = fields_map.get('state')
        if state and state.type == 'selection':
            return 'state'
        views = self.env['ir.ui.view'].sudo().search(
            [('model', '=', self.model_name), ('type', '=', 'form'),
             ('mode', '=', 'primary')], order='priority, id')
        for view in views:
            try:
                root = etree.fromstring(view.arch.encode())
            except Exception:
                continue
            for node in root.findall('.//header/field[@widget="statusbar"]'):
                field = fields_map.get(node.get('name'))
                if field and field.type == 'selection':
                    return node.get('name')
        return 'state'

    def _get_state_field_name(self):
        """Name of the status field of the target model."""
        self.ensure_one()
        Model = self.env.registry.get(self.model_name) if self.model_name else None
        if Model is not None and self.state_field not in Model._fields:
            # stored before the target model was loaded, or the field is gone
            return self._detect_state_field()
        return self.state_field or 'state'

    def _ensure_state_field(self):
        """Create the technical field driving the button visibility on the target model."""
        IrModelFields = self.env['ir.model.fields'].sudo()
        for workflow in self:
            if not workflow.model_id or not workflow.model_name:
                continue
            for field_name, label in (('x_approval_state', 'Approval State'),
                                      ('x_approval_stage', 'Approval Stage'),
                                      ('x_approval_next_stage', 'Next Approval Stage')):
                existing = IrModelFields.search([
                    ('model', '=', workflow.model_name),
                    ('name', '=', field_name),
                ], limit=1)
                if existing:
                    continue
                IrModelFields.create({
                    'name': field_name,
                    'field_description': label,
                    'model_id': workflow.model_id.id,
                    'ttype': 'char',
                    'state': 'manual',
                    'readonly': True,
                    'copied': False,
                    'store': True,
                })

    def _ensure_workflow_state_field(self):
        """حقل يدوي يجمع حالات النموذج ومراحل الموافقة في شريط واحد."""
        IrModelFields = self.env['ir.model.fields'].sudo()
        Selection = self.env['ir.model.fields.selection'].sudo()
        for workflow in self:
            if not workflow.model_id:
                continue
            field = IrModelFields.search([
                ('model', '=', workflow.model_name),
                ('name', '=', 'x_workflow_state'),
            ], limit=1)
            if not field:
                field = IrModelFields.create({
                    'name': 'x_workflow_state',
                    'field_description': 'Workflow State',
                    'model_id': workflow.model_id.id,
                    'ttype': 'selection',
                    'state': 'manual',
                    'readonly': True,
                    'copied': False,
                    'store': True,
                    'selection_ids': [(0, 0, {
                        'value': 'draft', 'name': 'Draft', 'sequence': 1})],
                })
            # الحقل مشترك بين كل مسارات النموذج، فنجمع قيم الجميع
            # حتى لا يحذف مسار قيم مسار آخر على نفس النموذج
            peers = self.search([('model_name', '=', workflow.model_name)])
            wanted = []
            seen = set()
            for line in (peers | workflow).stage_ids.sorted(lambda l: (l.sequence, l.id)):
                if line.line_type == 'state' and line.model_state_id.value:
                    value, label = line.model_state_id.value, line.model_state_id.label
                elif line.line_type == 'approval':
                    value, label = line._get_stage_state_value(), line.name or ''
                else:
                    continue
                if value in seen:
                    continue
                seen.add(value)
                wanted.append((value, label, line.sequence))
            # create/write/unlink of ir.model.fields.selection each rebuild the
            # registry (setup_models), and with several workers every other worker
            # then reloads the whole registry. So the base ORM methods are used and:
            # - a label/order change only clears the cache: the web client reads the
            #   labels and their order from get_field_selection (ormcached, from DB);
            # - only a new or removed value needs one rebuild, for value validation.
            existing = {v.value: v for v in Selection.search([('field_id', '=', field.id)])}
            to_create = []
            updated = False
            for value, label, sequence in wanted:
                name = label or value
                current = existing.get(value)
                if not current:
                    to_create.append({'field_id': field.id, 'value': value,
                                      'name': name, 'sequence': sequence})
                elif current.name != name or current.sequence != sequence:
                    models.Model.write(current, {'name': name, 'sequence': sequence})
                    updated = True
            obsolete = Selection.browse([
                rec.id for value, rec in existing.items()
                if value not in {v for v, _l, _s in wanted}])
            if obsolete:
                obsolete._process_ondelete()
                models.Model.unlink(obsolete)
            if to_create:
                models.Model.create(Selection, to_create)
            if to_create or obsolete:
                self.env.flush_all()
                self.env.registry.setup_models(self.env.cr)
            elif updated:
                self.env.flush_all()
                model_field = self.env[workflow.model_name]._fields.get('x_workflow_state')
                if model_field is not None:
                    # this worker's copy, used to validate and group the values
                    model_field.selection = Selection._get_selection_data(field.id)
                self.env.registry.clear_cache()

    def action_apply_configuration(self):
        """Create the technical field, load the model states, regenerate the view."""
        self._ensure_state_field()
        self._sync_model_states()
        self._populate_state_lines()
        self._ensure_workflow_state_field()
        self._sync_form_view()
        self._sync_existing_records()
        return True

    def _sync_existing_records(self):
        """Put the records already waiting before an approval stage on that stage.

        Records only sync when their state changes, so the ones already sitting
        on a state followed by an approval stage would never show its buttons.
        """
        # many records can move at once: queue the mails instead of sending
        # them one by one inside the request
        Approval = self.env['approval.workflow.record'].sudo().with_context(
            approval_bulk_sync=True, mail_notify_force_send=False)
        for workflow in self:
            if not workflow.active or workflow.model_name not in self.env:
                continue
            Model = self.env[workflow.model_name].sudo().with_context(active_test=False)
            state_field = workflow._get_state_field_name()
            if (state_field not in Model._fields
                    or 'x_approval_next_stage' not in Model._fields):
                continue
            lines = workflow._get_ordered_lines()
            values = [line.model_state_id.value
                      for line, following in zip(lines, lines[1:])
                      if line.line_type == 'state' and line.model_state_id.value
                      and following.line_type == 'approval']
            # also refresh the records still carrying a marker, it may be stale
            domain = [('x_approval_next_stage', 'not in', [False, ''])]
            if values:
                domain = ['|', (state_field, 'in', values)] + domain
            for record in Model.search(domain):
                Approval._sync_for_record(record)

    def _populate_state_lines(self):
        """Insert the states of the target model as ordered lines."""
        Stage = self.env['approval.workflow.stage'].sudo()
        for workflow in self:
            states = self.env['approval.model.state'].sudo().search(
                [('model_name', '=', workflow.model_name),
                 ('field_name', '=', workflow._get_state_field_name())],
                order='sequence, id')
            existing = workflow.stage_ids.filtered(lambda l: l.line_type == 'state')
            known = existing.mapped('model_state_id').ids
            excluded = workflow.excluded_state_ids.ids
            for index, state in enumerate(states):
                if state.id in known or state.id in excluded:
                    continue
                Stage.create({
                    'workflow_id': workflow.id,
                    'name': state.label,
                    'line_type': 'state',
                    'model_state_id': state.id,
                    'sequence': (index + 1) * 100,
                    'approver_type': 'user',
                    'approval_method': 'one',
                })

    def _sync_model_states(self):
        """Load the selectable states of the target model, for the stages."""
        ModelState = self.env['approval.model.state']
        for workflow in self:
            ModelState._sync_model_states(
                workflow.model_name, workflow._get_state_field_name())

    def _get_blocked_methods(self):
        """Method names of the target model gated by this workflow."""
        self.ensure_one()
        if not self.blocked_methods:
            return []
        Model = self.env.registry.get(self.model_name)
        names = [m.strip() for m in self.blocked_methods.split(',') if m.strip()]
        return [name for name in names if Model and hasattr(Model, name)]

    @api.depends('active')
    def _compute_state(self):
        for workflow in self:
            workflow.state = 'active' if workflow.active else 'inactive'

    def _inverse_state(self):
        for workflow in self:
            workflow.active = workflow.state == 'active'

    @api.depends('stage_ids')
    def _compute_stage_count(self):
        for workflow in self:
            workflow.stage_count = len(workflow.stage_ids)

    @api.constrains('condition', 'model_id')
    def _check_condition(self):
        for workflow in self:
            if not workflow.condition or not workflow.model_name:
                continue
            try:
                domain = safe_eval(workflow.condition)
                self.env[workflow.model_name].search_count(domain)
            except Exception as error:
                raise ValidationError(
                    _('The trigger criteria of "%(name)s" is not a valid domain: %(error)s',
                      name=workflow.name, error=error))

    def _match_record(self, record):
        """Whether this workflow applies to the given record."""
        self.ensure_one()
        if record._name != self.model_name:
            return False
        if not self.condition or self.condition.strip() in ('[]', ''):
            return True
        try:
            domain = safe_eval(self.condition)
        except Exception:
            return False
        return bool(record.filtered_domain(domain))

    @api.model
    def _get_workflow_for_record(self, record):
        """First active workflow whose criteria match the record."""
        workflows = self.search([('model_name', '=', record._name)], order='sequence, id')
        for workflow in workflows:
            if workflow.stage_ids and workflow._match_record(record):
                return workflow
        return self.browse()

    # ------------------------------------------------------------------
    # Form view generation
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    def _get_condition_client_expression(self):
        """ترجمة معيار التشغيل إلى تعبير يفهمه العميل على سجل غير محفوظ.

        تعاد (التعبير, الحقول المستعملة)، أو False إذا تعذّرت الترجمة
        فنعتمد وقتها على المؤشر المحسوب بعد الحفظ.
        """
        self.ensure_one()
        if not self.condition or self.condition.strip() in ('[]', ''):
            return '', []
        try:
            domain = safe_eval(self.condition)
        except Exception:
            return False
        model = self.env.get(self.model_name)
        if model is None:
            return False
        # عوامل المجال تُترجم إلى ما يفهمه العميل: '=' تصبح '=='
        operators = {'=': '==', '==': '==', '!=': '!=', '<>': '!=',
                     '>': '>', '>=': '>=', '<': '<', '<=': '<=',
                     'in': 'in', 'not in': 'not in'}
        parts, roots = [], []
        for leaf in domain:
            if isinstance(leaf, str):
                if leaf == '&':
                    continue
                # '|' و '!' تحتاج ترتيبا لا نترجمه هنا
                return False
            if not isinstance(leaf, (list, tuple)) or len(leaf) != 3:
                return False
            path, operator, value = leaf
            if operator not in operators:
                return False
            root = path.split('.')[0]
            field = model._fields.get(root)
            if not field:
                return False
            if '.' in path:
                if not field.comodel_name:
                    return False
                try:
                    ids = self.env[field.comodel_name].sudo().search(
                        [(path.split('.', 1)[1], operator, value)]).ids
                except Exception:
                    return False
                parts.append('%s in %s' % (root, ids or [0]))
            else:
                parts.append('%s %s %s' % (root, operators[operator], repr(value)))
            if root not in roots:
                roots.append(root)
        if not parts:
            return False
        return ' and '.join(parts), roots

    def _get_approval_buttons(self):
        """زر واحد لكل مرحلة، يظهر فقط عندما تكون هي الخطوة التالية للسجل."""
        self.ensure_one()
        Model = self.env.registry.get(self.model_name)
        lines = self._get_ordered_lines()
        stages = lines.filtered(lambda l: l.line_type == 'approval')
        if not stages or not Model:
            return '<field name="x_approval_state" invisible="1"/>'

        parts = ['<field name="x_approval_state" invisible="1"/>',
                 '<field name="x_approval_stage" invisible="1"/>',
                 '<field name="x_approval_next_stage" invisible="1"/>',
                 '<field name="x_workflow_state" invisible="1"/>']
        state_field = self._get_state_field_name()
        if state_field in Model._fields:
            parts.append('<field name="%s" invisible="1"/>' % state_field)

        # على السجل الجديد لا توجد قيم محسوبة بعد، لذلك نعتمد على الحالة الأولى
        # مع معيار تشغيل نترجمه إلى تعبير يفحصه العميل قبل الحفظ
        first_state = lines.filtered(lambda l: l.line_type == 'state')[:1]
        new_record_stage = self.env['approval.workflow.stage']
        condition_expr = ''
        expression = self._get_condition_client_expression()
        if first_state and expression is not False:
            condition_expr, condition_fields = expression
            for name in condition_fields:
                parts.append('<field name="%s" invisible="1"/>' % name)
            following = lines.filtered(
                lambda l: (l.sequence, l.id) > (first_state.sequence, first_state.id))
            if following and following[0].line_type == 'approval':
                new_record_stage = following[0]

        new_record_clause = ''
        if new_record_stage and first_state.model_state_id.value:
            new_record_clause = "(not id and %s == '%s'%s)" % (
                state_field, first_state.model_state_id.value,
                ' and (%s)' % condition_expr if condition_expr else '')

        for stage in stages:
            label = stage.button_label or stage.name
            visible = ["x_approval_next_stage == '%s'" % stage.id]
            if stage == new_record_stage and new_record_clause:
                visible.append(new_record_clause)
            invisible = 'not (%s)' % ' or '.join(visible)
            context = "{'approval_stage_id': %s}" % stage.id
            parts.append(
                '<button name="action_approval_approve" type="object" string="%s" '
                'class="btn-primary" context="%s" invisible="%s"/>'
                % (label, context, invisible))
            if stage.allow_reject:
                parts.append(
                    '<button name="action_approval_reject_wizard" type="object" '
                    'string="Reject" context="%s" invisible="%s"/>' % (context, invisible))
            if stage.allow_send_back:
                parts.append(
                    '<button name="action_approval_send_back_wizard" type="object" '
                    'string="Send Back" context="%s" invisible="%s"/>' % (context, invisible))
            if stage.allow_reset_draft:
                parts.append(
                    '<button name="action_approval_reset_draft" type="object" '
                    'string="Reset to Draft" context="%s" invisible="%s"/>'
                    % (context, invisible))
        return '\n            ' + '\n            '.join(parts)

    def _get_ordered_lines(self):
        """أسطر المسار بالترتيب: حالات النموذج ومراحل الموافقة."""
        self.ensure_one()
        return self.stage_ids.sorted(lambda l: (l.sequence, l.id))

    def _get_line_value(self, line):
        """القيمة التي تمثل السطر في شريط الحالة."""
        if line.line_type == 'state':
            return line.model_state_id.value or ''
        return line._get_stage_state_value()

    def _get_position_value(self, record):
        """موضع السجل الحالي في المسار."""
        self.ensure_one()
        if 'x_workflow_state' in record._fields and record.x_workflow_state:
            return record.x_workflow_state
        state_field = self._get_state_field_name()
        return record[state_field] if state_field in record._fields else ''

    def _get_next_stage_for_record(self, record):
        """مرحلة الموافقة التالية مباشرة بعد موضع السجل، إن وجدت."""
        self.ensure_one()
        lines = self._get_ordered_lines()
        position = self._get_position_value(record)
        index = -1
        for offset, line in enumerate(lines):
            if self._get_line_value(line) == position:
                index = offset
                break
        if index < 0:
            return self.env['approval.workflow.stage']
        for line in lines[index + 1:]:
            if line.line_type == 'approval':
                return line
            return self.env['approval.workflow.stage']
        return self.env['approval.workflow.stage']

    def _get_hidden_state_condition(self):
        """أزرار النموذج مخفية طالما هناك مرحلة موافقة هي الخطوة التالية."""
        self.ensure_one()
        conditions = ["x_approval_next_stage"]
        lines = self._get_ordered_lines()
        first_state = lines.filtered(lambda l: l.line_type == 'state')[:1]
        Model = self.env.registry.get(self.model_name)
        # مع وجود معيار تشغيل نترجمه إلى تعبير يفحصه العميل قبل الحفظ،
        # فلا تختفي أزرار النموذج إلا على السجلات المشمولة بالمعيار
        expression = self._get_condition_client_expression()
        if (first_state and Model and expression is not False
                and self._get_state_field_name() in Model._fields):
            condition_expr = expression[0]
            following = lines.filtered(
                lambda l: (l.sequence, l.id) > (first_state.sequence, first_state.id))
            if following and following[0].line_type == 'approval':
                conditions.append("(not id and %s == '%s'%s)" % (
                    self._get_state_field_name(), first_state.model_state_id.value,
                    ' and (%s)' % condition_expr if condition_expr else ''))
        return ' or '.join(conditions)

    def _get_removed_states(self):
        """States of the model that are no longer lines of this workflow.

        They stay in the code of the model, they are only hidden in the backend.
        """
        self.ensure_one()
        kept = self.stage_ids.filtered(
            lambda l: l.line_type == 'state').mapped('model_state_id')
        if not kept:
            return self.env['approval.model.state']
        all_states = self.env['approval.model.state'].sudo().search(
            [('model_name', '=', self.model_name),
             ('field_name', '=', self._get_state_field_name())])
        return (all_states - kept) | self.excluded_state_ids

    def _get_statusbar_override(self, base_view):
        """Show in the statusbar only the states kept as lines of this workflow."""
        self.ensure_one()
        if not base_view:
            return ''
        state_lines = self.stage_ids.filtered(lambda l: l.line_type == 'state')
        states = state_lines.mapped('model_state_id')
        if not states:
            return ''
        field_name = states[0].field_name or 'state'
        try:
            root = etree.fromstring(base_view.arch.encode())
        except Exception:
            return ''
        if root.find('.//header/field[@name="%s"]' % field_name) is None:
            return ''
        ordered_lines = self.stage_ids.sorted(lambda l: (l.sequence, l.id))
        values = []
        for line in ordered_lines:
            if line.line_type == 'state' and line.model_state_id.value:
                values.append(line.model_state_id.value)
            elif line.line_type == 'approval':
                values.append(line._get_stage_state_value())
        values = ','.join(values)
        # مع معيار تشغيل: شريط المسار للسجلات المشمولة فقط، وغيرها يبقى بشريطه الأصلي
        expression = self._get_condition_client_expression()
        condition = expression[0] if expression is not False else ''
        if condition and not self.stage_ids.filtered(lambda l: l.line_type == 'approval'):
            # حقول المعيار تُحقن مع أزرار الموافقة، فإن لم توجد نعود للسلوك العام
            condition = ''
        native_invisible = condition or '1'
        custom = ('<field name="x_workflow_state" widget="statusbar" '
                  'statusbar_visible="%s"%s/>' % (
                      values,
                      ' invisible="not (%s)"' % condition if condition else ''))
        # الإضافة لا الاستبدال: مسارات أخرى على نفس النموذج تخفي الشريط أيضا
        return ('\n    <xpath expr="//header/field[@name=\'%s\']" position="attributes">'
                '\n        <attribute name="invisible" add="%s" separator="or"/>'
                '\n    </xpath>'
                '\n    <xpath expr="//header/field[@name=\'%s\']" position="after">'
                '\n        %s'
                '\n    </xpath>' % (field_name, native_invisible, field_name, custom))

    def _get_native_button_overrides(self, base_view):
        """Hide the buttons of the target model while the record awaits approval."""
        self.ensure_one()
        if not base_view:
            return ''
        try:
            root = etree.fromstring(base_view.arch.encode())
        except Exception:
            return ''
        condition = self._get_hidden_state_condition()
        removed = self._get_removed_states().mapped('value')
        buttons = [b for b in root.findall('.//header/button')
                   if b.get('name') and not b.get('name').startswith('action_approval_')]
        # عدد التكرارات لكل زر: النموذج قد يحتوي على أكثر من زر بنفس الاسم
        totals = {}
        for button in buttons:
            totals[button.get('name')] = totals.get(button.get('name'), 0) + 1
        parts, occurrence = [], {}
        for button in buttons:
            name = button.get('name')
            occurrence[name] = occurrence.get(name, 0) + 1
            button_condition = condition
            if removed and self._button_belongs_to_states(
                    button, removed, self._get_state_field_name()):
                # الزر يخص حالة أزيلت من المسار: يخفى دون تعديل النموذج
                button_condition = '1 == 1'
            if totals[name] > 1:
                expr = "(//header/button[@name='%s'])[%s]" % (name, occurrence[name])
            else:
                expr = "//header/button[@name='%s']" % name
            parts.append(
                '\n    <xpath expr="%s" position="attributes">'
                '\n        <attribute name="invisible" add="%s" separator=" or "/>'
                '\n    </xpath>' % (expr, button_condition))
        return ''.join(parts)

    def _button_belongs_to_states(self, button, removed_values, state_field='state'):
        """Whether a button is only shown on states removed from the workflow."""
        invisible = button.get('invisible') or ''
        if not invisible:
            return False
        # "state != 'draft'" means the button only shows in the draft state
        matches = re.findall(r"\b%s\s*!=\s*'([^']+)'" % re.escape(state_field), invisible)
        return bool(matches) and all(value in removed_values for value in matches)

    def _get_form_view_arch(self, base_view=None):
        """Inject the buttons into the existing header, or create one."""
        self.ensure_one()
        buttons = self._get_approval_buttons()
        has_header = bool(base_view) and '<header' in (base_view.arch_db or '')
        if has_header:
            header_part = """
    <xpath expr="//header" position="inside">%s
    </xpath>""" % buttons
        else:
            header_part = """
    <xpath expr="//sheet" position="before">
        <header>%s
        </header>
    </xpath>""" % buttons
        stat_button = """
    <xpath expr="//sheet" position="inside">
        <div class="oe_button_box" name="approval_button_box">
            <button class="oe_stat_button" type="object" icon="fa-history" name="action_view_approval_history" string="Approvals"/>
        </div>
    </xpath>"""
        hide_native = self._get_native_button_overrides(base_view)
        statusbar = self._get_statusbar_override(base_view)
        return "<data>%s%s%s%s\n</data>" % (
            header_part, statusbar, hide_native, stat_button)

    def _sync_form_view(self):
        """Create or update the inherited form view carrying the approval buttons."""
        View = self.env['ir.ui.view'].sudo()
        for workflow in self:
            if not workflow.inject_buttons or not workflow.model_name or not workflow.active:
                if workflow.view_id:
                    workflow.view_id.unlink()
                    workflow.view_id = False
                continue
            Model = self.env.registry.get(workflow.model_name)
            required = ('x_approval_state', 'x_approval_stage', 'x_workflow_state')
            if not Model or any(f not in Model._fields for f in required):
                # the technical field is not in the registry yet: the view would
                # not validate. It lands on the next load, once the field exists.
                continue
            base_view = View.search(
                [('model', '=', workflow.model_name), ('type', '=', 'form'),
                 ('mode', '=', 'primary')], limit=1, order='priority, id')
            if not base_view:
                continue
            values = {
                'name': 'approval.buttons.%s' % workflow.model_name,
                'model': workflow.model_name,
                'inherit_id': base_view.id,
                'mode': 'extension',
                'priority': 99,
                'arch_db': workflow._get_form_view_arch(base_view),
                'active': True,
            }
            if workflow.view_id:
                view = workflow.view_id
                # this runs on every registry load of every worker: writing an
                # unchanged view re-validates the whole form and clears the caches
                if any(view[key] != (base_view if key == 'inherit_id' else value)
                       for key, value in values.items()):
                    view.write(values)
            else:
                workflow.view_id = View.create(values)

    @api.model_create_multi
    def create(self, vals_list):
        workflows = super().create(vals_list)
        workflows._ensure_state_field()
        workflows._sync_model_states()
        workflows._populate_state_lines()
        workflows._sync_form_view()
        return workflows

    def write(self, vals):
        res = super().write(vals)
        if {'model_id', 'inject_buttons', 'active', 'state', 'state_field'} & set(vals):
            self._ensure_state_field()
            self._sync_model_states()
            self._populate_state_lines()
            self._sync_form_view()
        return res

    def _schedule_stage_sync(self):
        """Sync the statusbar values and the records once, right before the commit.

        Lines are often written one by one (form save, drag & drop resequence
        through /web/dataset/resequence). Each sync may rebuild the registry and
        walk every record of the model, so running it per line times out.
        """
        if not self:
            return
        precommit = self.env.cr.precommit
        pending = precommit.data.setdefault('approval.workflow.stage_sync', set())
        if not pending:
            env = self.env

            def _run():
                ids = precommit.data.pop('approval.workflow.stage_sync', set())
                workflows = env['approval.workflow'].browse(list(ids)).exists()
                workflows._ensure_workflow_state_field()
                # buttons and statusbar order follow the lines (no-op if unchanged)
                workflows._sync_form_view()
                workflows._sync_existing_records()

            precommit.add(_run)
        pending.update(self.ids)

    def unlink(self):
        self.mapped('view_id').unlink()
        return super().unlink()

    def action_view_stages(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Approval Stages'),
            'res_model': 'approval.workflow.stage',
            'view_mode': 'list,form',
            'domain': [('workflow_id', '=', self.id)],
            'context': {'default_workflow_id': self.id},
        }
