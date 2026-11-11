"""迁移时新写的小文件：原 capabilities.py（模块调用协议）不搬，这里只留 formula_components 用到的 _formula_input，原文照抄。
store 参数在新系统里是 lab.sci.formulation 里的一个小适配器（有 get_formula / get_batch / registry 三个方法）。"""


class CapabilityError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def _formula_input(item, store):
    batch = item.get('batch')
    if item.get('batch_id'):
        if store is None:
            raise ValueError('按工单编号调用时，请指定项目库。')
        try:batch = store.get_batch(item['batch_id'])
        except ValueError as exc:raise CapabilityError('RECORD_NOT_FOUND', str(exc)) from exc
    formula, registry = item.get('formula'), item.get('registry')
    if item.get('formula_id'):
        if store is None:
            raise ValueError('按配方编号调用时，请指定项目库。')
        try:saved = store.get_formula(item['formula_id'])
        except ValueError as exc:raise CapabilityError('RECORD_NOT_FOUND', str(exc)) from exc
        formula = saved['data']
    if formula is None and batch:
        formula = batch['formula_data']
    if registry is None and batch:
        registry = batch.get('registry_snapshot')
    if not isinstance(formula, dict):
        raise ValueError('请提供配方对象、配方编号或工单编号。')
    if registry is None and store is not None:
        registry = store.registry()
    if not isinstance(registry, dict):
        raise ValueError('请提供原料与预混定义，或指定项目库。')
    return formula, registry, batch
