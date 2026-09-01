import { http, unwrap } from './http';

export interface DataSourceItem {
  id: number;
  group_name: string;
  name: string;
  object_name: string;
  object_type: string;
  description: string;
  enabled: boolean;
  sort_order: number;
}

/** 数据源列表（对应前端「数据源选择器」分组） */
export const fetchDataSources = () =>
  unwrap<DataSourceItem[]>(http.get('/data-sources'));
