import z from 'schemastery';
export declare const name = "@dsh-external/dsh-blender-plugin";
export declare const inject: string[];
export interface Config {
    port: number;
    autoStart: boolean;
}
export declare const Config: z<Schemastery.ObjectS<{
    port: z<number, number>;
    autoStart: z<boolean, boolean>;
}>, Schemastery.ObjectT<{
    port: z<number, number>;
    autoStart: z<boolean, boolean>;
}>>;
export declare function apply(ctx: any, config: Config): void;
